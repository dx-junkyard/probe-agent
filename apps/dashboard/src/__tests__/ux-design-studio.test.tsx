/// <reference types="vitest/globals" />
// Issue #409 (Epic #405): UX Design Studio tests.
//
// `docs/ux-design-lineage.md` §0 invariant 9 is the thing every test here
// ultimately protects: the client re-derives no state. Everything below
// exercises what the Studio does with values the server already decided --
// never a computation the Studio performs itself (that discipline lives in
// `model.ts` and is unit-tested directly at the bottom of this file).

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type {
  UxJourneyDetailOut, UxJourneyDiffOut, UxJourneyListOut, UxJourneyOut,
  UxRequirementDetailOut, UxRequirementListOut, UxRequirementOut,
  SolutionDesignDetailOut, SolutionDesignHandoffOut, SolutionDesignListOut, SolutionDesignOut,
} from "@/api/types";
import {
  AUTHORSHIP_LABEL, DIFF_CHANGE_KIND_LABEL, REVISION_STATE_LABEL,
} from "@/components/ux-design/model";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <MemoryRouter initialEntries={["/ux-design-studio"]}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

async function renderPage() {
  const { default: UxDesignStudioPage } = await import("@/pages/ux-design-studio");
  return render(<UxDesignStudioPage />, { wrapper });
}

// --- fixtures ---------------------------------------------------------------

function journeyListOut(journeys: UxJourneyOut[], overrides: Partial<UxJourneyListOut> = {}): UxJourneyListOut {
  return {
    system_id: 1, generated_at: 1000, journeys, degraded_sections: [], degraded_detail: {}, ...overrides,
  };
}

function journeyOut(overrides: Partial<UxJourneyOut> = {}): UxJourneyOut {
  return {
    id: 1, system_id: 1, journey_key: "checkout-to-be", perspective: "to_be",
    baseline_mode: "undecided", baseline_journey_id: null, baseline_journey_key: null,
    baseline_state: "absent", current_revision_id: 1, current_revision_number: 1,
    title: "チェックアウトの目標像", design_status: "proposed", recheck_state: "current",
    created_by: "dev", created_at: 1000, updated_at: 1000, ...overrides,
  };
}

function journeyDetailOut(overrides: Partial<UxJourneyDetailOut> = {}): UxJourneyDetailOut {
  const base = journeyOut();
  return {
    ...base,
    current_revision: {
      id: 1, journey_id: base.id, revision_number: 1, title: base.title,
      beneficiary: "初めての利用者", usage_context: "スマートフォン", entry_trigger: "カートを開く",
      value_arrival: "注文が完了する", summary: "チェックアウトを 1 画面で終える",
      content_digest: "digest-1", authored_by_kind: "developer", decision_method: "manual",
      intelligence_run_id: null, change_note: "", created_by: "dev", created_at: 1000,
      revision_state: "current", superseded_by_id: null,
      steps: [
        {
          id: 1, step_key: "enter-address", step_order: 1, user_intent: "住所を入力する",
          system_response: "住所候補を提示する", success_criteria: "1 回で確定できる",
          failure_mode: "候補が出ない", recovery_path: "手入力を許可する",
          evidence_expectation: "住所確定までの所要時間が短縮している",
          evidence_source_kind: "runtime_trace", content_digest: "step-digest-1",
        },
        {
          id: 2, step_key: "confirm-order", step_order: 2, user_intent: "注文内容を確認する",
          system_response: "確認画面を表示する", success_criteria: "誤り率が下がる",
          failure_mode: "", recovery_path: "", evidence_expectation: "",
          evidence_source_kind: "none", content_digest: "step-digest-2",
        },
      ],
    },
    upstream_refs: [], artifact_references: [], decisions: [],
    degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function requirementListOut(
  requirements: UxRequirementOut[], overrides: Partial<UxRequirementListOut> = {},
): UxRequirementListOut {
  return {
    system_id: 1, generated_at: 1000, requirements, degraded_sections: [], degraded_detail: {}, ...overrides,
  };
}

function requirementOut(overrides: Partial<UxRequirementOut> = {}): UxRequirementOut {
  return {
    id: 10, system_id: 1, requirement_key: "single-page-checkout", requirement_kind: "functional",
    current_revision_id: 1, current_revision_number: 1, statement: "1 画面で完了できる",
    design_status: "proposed", recheck_state: "current", created_by: "dev",
    created_at: 1000, updated_at: 1000, ...overrides,
  };
}

function requirementDetailOut(overrides: Partial<UxRequirementDetailOut> = {}): UxRequirementDetailOut {
  const base = requirementOut();
  return {
    ...base,
    current_revision: {
      id: 1, requirement_id: base.id, revision_number: 1, requirement_kind: base.requirement_kind,
      statement: base.statement, rationale: "離脱を減らすため", constraint_text: "",
      out_of_scope_note: "", content_digest: "req-digest-1", authored_by_kind: "developer",
      decision_method: "manual", intelligence_run_id: null, change_note: "", created_by: "dev",
      created_at: 1000, revision_state: "current", superseded_by_id: null,
      acceptance_criteria: [],
    },
    step_links: [
      {
        id: 1, requirement_id: base.id, journey_id: 1, journey_key: "checkout-to-be",
        step_key: "enter-address", step_label: null, captured_journey_revision_id: 1,
        captured_step_digest: "step-digest-1", target_resolution: "resolved",
        recheck_state: "current", note: "", decision_method: "manual",
        created_by: "dev", created_at: 1000, superseded_by_id: null,
      },
    ],
    artifact_references: [], decisions: [], degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function solutionDesignListOut(
  designs: SolutionDesignOut[], overrides: Partial<SolutionDesignListOut> = {},
): SolutionDesignListOut {
  return { system_id: 1, generated_at: 1000, designs, degraded_sections: [], degraded_detail: {}, ...overrides };
}

function solutionDesignOut(overrides: Partial<SolutionDesignOut> = {}): SolutionDesignOut {
  return {
    id: 20, system_id: 1, design_key: "address-autofill", title: "住所オートコンプリート",
    summary: "候補を提示して入力を減らす", adopted_option_key: "opt-a", option_count: 1,
    created_by: "dev", created_at: 1000, updated_at: 1000, ...overrides,
  };
}

function solutionDesignDetailOut(overrides: Partial<SolutionDesignDetailOut> = {}): SolutionDesignDetailOut {
  const base = solutionDesignOut();
  return {
    ...base,
    options: [
      {
        id: 100, solution_design_id: base.id, option_key: "opt-a", option_order: 1,
        title: "外部 API で補完", approach: "郵便番号 API を呼ぶ", tradeoffs: "外部依存が増える",
        risks: "API 障害時に劣化する", content_digest: "opt-digest-1", authored_by_kind: "developer",
        decision_method: "manual", intelligence_run_id: null, option_status: "adopted",
        created_by: "dev", created_at: 1000, revision_state: "current", superseded_by_id: null,
      },
    ],
    requirement_links: [
      {
        id: 200, solution_design_id: base.id, requirement_id: 10, requirement_key: "single-page-checkout",
        captured_requirement_revision_id: 1, captured_digest: "req-digest-1", link_state: "current",
        stale_reason: null, note: "", decision_method: "manual", created_by: "dev",
        created_at: 1000, superseded_by_id: null,
      },
    ],
    target_links: [
      {
        id: 300, solution_design_id: base.id, option_id: 100, option_key: "opt-a",
        target_kind: "capability", target_ref: "42", target_row_id: 42, target_name: "住所入力",
        captured_digest: "cap-digest-1", captured_snapshot_id: null, link_state: "current",
        stale_reason: null, review_required: false, note: "", decision_method: "manual",
        created_by: "dev", created_at: 1000, superseded_by_id: null,
      },
    ],
    decisions: [], degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function handoffOut(overrides: Partial<SolutionDesignHandoffOut> = {}): SolutionDesignHandoffOut {
  const design = solutionDesignDetailOut();
  return {
    system_id: 1, solution_design_id: design.id, design_key: design.design_key, generated_at: 1000,
    handoff_state: "complete", adopted_option: design.options[0], target_links: design.target_links,
    requirements: [requirementDetailOut()], node_decomposition_refs: [], probe_plan_refs: [],
    evaluation_policy_refs: {
      node: [{ policy_key: "node-p1", statement: "契約を満たす" }],
      flow_capability: [],
      ux_outcome: [{ policy_key: "ux-p1", statement: "所要時間が短縮する" }],
    },
    unresolved_references: [], degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

/** A tiny fake backend: exact-path overrides win, otherwise a small set of
 * sensible empty defaults so components that always fetch (e.g. the
 * Requirement detail's Solution Design lookup) do not need to be mocked in
 * every test that never opens that panel. */
function mockGet(overrides: Record<string, unknown>) {
  mockApi.get.mockImplementation((path: string) => {
    if (path in overrides) {
      const v = overrides[path];
      return v instanceof Error ? Promise.reject(v) : Promise.resolve(v);
    }
    if (path === "/ux-design/journeys") return Promise.resolve(journeyListOut([]));
    if (path === "/ux-design/requirements") return Promise.resolve(requirementListOut([]));
    if (path === "/solution-designs") return Promise.resolve(solutionDesignListOut([]));
    return new Promise(() => {}); // never resolves -> renders as "loading"
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

// --- revision history / revision-to-revision diff ---------------------------

describe("版の履歴と版どうしの差分", () => {
  function journeyRevisionListOut(count: number) {
    const base = journeyDetailOut();
    const one = base.current_revision!;
    return {
      system_id: 1, journey_id: base.id, journey_key: base.journey_key, generated_at: 1,
      revisions: Array.from({ length: count }, (_, i) => ({
        ...one, id: i + 1, revision_number: i + 1,
        revision_state: i === count - 1 ? "current" : "superseded",
        authored_by_kind: i === 0 ? "reasoning_model" : "developer",
        change_note: i === 0 ? "" : `第 ${i + 1} 版`,
      })),
      degraded_sections: [], degraded_detail: {},
    };
  }

  test("過去の版は上書きされず、現行版と別の文言で読める", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ journey_key: "checkout-to-be" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
      "/ux-design/journeys/checkout-to-be/revisions": journeyRevisionListOut(2),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    const list = await screen.findByTestId("ux-revision-history-list");
    expect(within(list).getByText("#1")).toBeInTheDocument();
    expect(within(list).getByText("#2")).toBeInTheDocument();
    // The append-only rule is visible: the older revision is still readable and
    // is labelled differently from the current one.
    expect(within(list).getByText(REVISION_STATE_LABEL.superseded)).toBeInTheDocument();
    expect(within(list).getByText(REVISION_STATE_LABEL.current)).toBeInTheDocument();
  });

  test("執筆者が AI の版も、確認とは別の軸として表示される", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ journey_key: "checkout-to-be" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
      "/ux-design/journeys/checkout-to-be/revisions": journeyRevisionListOut(2),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    const list = await screen.findByTestId("ux-revision-history-list");
    expect(within(list).getByText(AUTHORSHIP_LABEL.reasoning_model)).toBeInTheDocument();
    expect(within(list).getByText(AUTHORSHIP_LABEL.developer)).toBeInTheDocument();
  });

  test("版が 1 つだけのときは「差分なし」ではなく比較相手がない旨を出す", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ journey_key: "checkout-to-be" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
      "/ux-design/journeys/checkout-to-be/revisions": journeyRevisionListOut(1),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    const note = await screen.findByTestId("ux-journey-revision-diff-single");
    expect(note.textContent).toContain("比較できる相手");
    expect(note.textContent).not.toContain("差分なし");
  });

  test("版どうしの差分は server が決めた change_kind をそのまま描画する", async () => {
    const diff: UxJourneyDiffOut = {
      system_id: 1, journey_id: 1, journey_key: "checkout-to-be", generated_at: 1,
      diff_state: "available",
      from_revision_id: 1, from_revision_number: 1, to_revision_id: 2, to_revision_number: 2,
      steps: [
        { step_key: "enter-address", change_kind: "changed", from_step: null, to_step: null },
        { step_key: "confirm-order", change_kind: "unchanged", from_step: null, to_step: null },
        { step_key: "pick-slot", change_kind: "added", from_step: null, to_step: null },
      ],
      degraded_sections: [], degraded_detail: {},
    };
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ journey_key: "checkout-to-be" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
      "/ux-design/journeys/checkout-to-be/revisions": journeyRevisionListOut(2),
      "/ux-design/journeys/checkout-to-be/diff?from_revision=1&to_revision=2": diff,
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    const entries = await screen.findByTestId("ux-revision-diff-entries");
    expect(within(entries).getByText("enter-address")).toBeInTheDocument();
    expect(within(entries).getByText(DIFF_CHANGE_KIND_LABEL.changed)).toBeInTheDocument();
    expect(within(entries).getByText(DIFF_CHANGE_KIND_LABEL.unchanged)).toBeInTheDocument();
    expect(within(entries).getByText(DIFF_CHANGE_KIND_LABEL.added)).toBeInTheDocument();
  });

  test("履歴の取得に失敗しても本文は消えず、再試行が出る", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ journey_key: "checkout-to-be" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
      "/ux-design/journeys/checkout-to-be/revisions": new ApiError(500, "boom"),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    expect(await screen.findByTestId("ux-journey-revision-history-error")).toBeInTheDocument();
    // The rest of the Journey detail is still on screen.
    expect(screen.getByTestId("ux-journey-detail")).toBeInTheDocument();
  });
});

// --- six display states ------------------------------------------------------

describe("六つの表示状態はそれぞれ別の文言を持つ", () => {
  test("as_is と to_be は別の文言で表示される", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([
        journeyOut({ journey_key: "checkout-as-is", perspective: "as_is", id: 2 }),
        journeyOut({ journey_key: "checkout-to-be", perspective: "to_be", id: 1 }),
      ]),
    });
    await renderPage();
    await screen.findByTestId("ux-journey-list");

    const asIsItem = screen.getByTestId("ux-journey-item-checkout-as-is");
    const toBeItem = screen.getByTestId("ux-journey-item-checkout-to-be");
    expect(within(asIsItem).getByText("現状 (as-is)")).toBeInTheDocument();
    expect(within(toBeItem).getByText("目標 (to-be)")).toBeInTheDocument();
    expect(within(asIsItem).queryByText("目標 (to-be)")).not.toBeInTheDocument();
  });

  test("stale は diff の changed とは別の文言で表示される (design_status は維持される)", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ recheck_state: "stale" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut({ recheck_state: "stale", design_status: "confirmed" }),
      "/ux-design/journeys/checkout-to-be/baseline-diff": {
        system_id: 1, journey_id: 1, journey_key: "checkout-to-be", generated_at: 1000,
        diff_state: "available", from_revision_id: 1, from_revision_number: 1,
        to_revision_id: 2, to_revision_number: 2,
        steps: [
          { step_key: "enter-address", change_kind: "changed", from_step: null, to_step: null },
        ],
        degraded_sections: [], degraded_detail: {},
      } satisfies UxJourneyDiffOut,
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    // "確定済み" (design_status) survives even though recheck is stale --
    // confirming and re-checking are independent facts (§2.5).
    const detail = await screen.findByTestId("ux-journey-detail");
    expect(within(detail).getByText("確定済み")).toBeInTheDocument();
    expect(within(detail).getByText("確定後に内容が変わりました。再確認してください")).toBeInTheDocument();
    const diffCard = screen.getByTestId("ux-journey-baseline-diff");
    expect(await within(diffCard).findByText("変更されました")).toBeInTheDocument();
    // Never the same sentence.
    expect(within(detail).getByText("確定後に内容が変わりました。再確認してください")).not.toHaveTextContent(
      "変更されました",
    );
  });

  test("unavailable は取得できませんでしたと表示され、空のページにはならない", async () => {
    mockGet({ "/ux-design/journeys": new Error("boom") });
    await renderPage();

    const errorCard = await screen.findByTestId("ux-journey-list-error");
    expect(within(errorCard).getByText(/取得できませんでした/)).toBeInTheDocument();
    expect(within(errorCard).getByRole("button", { name: "再試行" })).toBeInTheDocument();
  });

  test("not_applicable は greenfield と undecided で別文言になり、どちらも「差分なし」ではない", async () => {
    mockGet({
      "/ux-design/journeys": journeyListOut([journeyOut({ baseline_mode: "greenfield" })]),
      "/ux-design/journeys/checkout-to-be": journeyDetailOut({ baseline_mode: "greenfield" }),
      "/ux-design/journeys/checkout-to-be/baseline-diff": {
        system_id: 1, journey_id: 1, journey_key: "checkout-to-be", generated_at: 1000,
        diff_state: "not_applicable", from_revision_id: null, from_revision_number: null,
        to_revision_id: null, to_revision_number: null, steps: [], degraded_sections: [], degraded_detail: {},
      } satisfies UxJourneyDiffOut,
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

    const greenfieldNote = await screen.findByTestId("ux-journey-baseline-diff-not-applicable");
    expect(greenfieldNote).toHaveTextContent("新規システムとして宣言済み");
    expect(greenfieldNote).not.toHaveTextContent("差分なし");
  });
});

test("baseline_mode が undecided の not_applicable は greenfield と別の文言になる", async () => {
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut({ baseline_mode: "undecided" })]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut({ baseline_mode: "undecided" }),
    "/ux-design/journeys/checkout-to-be/baseline-diff": {
      system_id: 1, journey_id: 1, journey_key: "checkout-to-be", generated_at: 1000,
      diff_state: "not_applicable", from_revision_id: null, from_revision_number: null,
      to_revision_id: null, to_revision_number: null, steps: [], degraded_sections: [], degraded_detail: {},
    } satisfies UxJourneyDiffOut,
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));

  const note = await screen.findByTestId("ux-journey-baseline-diff-not-applicable");
  expect(note).not.toHaveTextContent("差分なし");
  expect(note).not.toHaveTextContent("新規システムとして宣言済み");
  expect(note).toHaveTextContent("まだ設定されていません");
});

// --- loading / empty / unknown / unavailable / not_applicable ---------------

test("loading / empty / unknown / unavailable / not_applicable は五つとも別の表示になる", async () => {
  // loading: the journeys query never resolves.
  mockApi.get.mockImplementation(() => new Promise(() => {}));
  const { unmount } = await renderPage();
  const loadingText = (await screen.findByTestId("ux-journey-list-loading")).textContent ?? "";
  unmount();

  // empty
  mockGet({ "/ux-design/journeys": journeyListOut([]) });
  const r2 = await renderPage();
  const emptyText = (await screen.findByTestId("ux-journey-list-empty")).textContent ?? "";
  r2.unmount();

  // unknown: developer has not yet decided a baseline (`absent`).
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut({ baseline_mode: "undecided" })]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut({ baseline_mode: "undecided", baseline_state: "absent" }),
  });
  const r3 = await renderPage();
  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));
  const unknownText = (await screen.findByTestId("ux-journey-baseline-state")).textContent ?? "";
  r3.unmount();

  // unavailable
  mockGet({ "/ux-design/journeys": new Error("boom") });
  const r4 = await renderPage();
  const unavailableText = (await screen.findByTestId("ux-journey-list-error")).textContent ?? "";
  r4.unmount();

  // not_applicable
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut({ baseline_mode: "greenfield" })]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut({ baseline_mode: "greenfield" }),
    "/ux-design/journeys/checkout-to-be/baseline-diff": {
      system_id: 1, journey_id: 1, journey_key: "checkout-to-be", generated_at: 1000,
      diff_state: "not_applicable", from_revision_id: null, from_revision_number: null,
      to_revision_id: null, to_revision_number: null, steps: [], degraded_sections: [], degraded_detail: {},
    } satisfies UxJourneyDiffOut,
  });
  const r5 = await renderPage();
  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));
  const notApplicableText = (await screen.findByTestId("ux-journey-baseline-diff-not-applicable")).textContent ?? "";
  r5.unmount();

  const texts = [loadingText, emptyText, unknownText, unavailableText, notApplicableText];
  expect(new Set(texts).size).toBe(texts.length);
});

// --- evidence / adoption copy ------------------------------------------------

test("evidence_source_kind が runtime_trace の Step は期待として表示され、成功としては表示されない", async () => {
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut()]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));
  fireEvent.click(await screen.findByTestId("ux-journey-step-item-enter-address"));

  const detail = await screen.findByTestId("ux-journey-step-detail-enter-address");
  expect(within(detail).getByText(/確認には runtime trace の観測が必要です\(未確認\)/)).toBeInTheDocument();
  expect(detail).not.toHaveTextContent("観測されました");
  expect(detail).not.toHaveTextContent("成功しました");
});

test("採用の記録は「適用」「デプロイ」を主張しない", async () => {
  mockGet({
    "/solution-designs": solutionDesignListOut([solutionDesignOut()]),
    "/solution-designs/address-autofill": solutionDesignDetailOut(),
    "/solution-designs/address-autofill/change-origins": {
      system_id: 1, solution_design_id: 20, design_key: "address-autofill", generated_at: 1000,
      origins: [], degraded_sections: [], degraded_detail: {},
    },
    "/solution-designs/address-autofill/handoff": handoffOut(),
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-design-studio-tab-solutions"));
  fireEvent.click(await screen.findByTestId("ux-solution-design-item-address-autofill"));

  const adopted = await screen.findByTestId("ux-solution-design-adopted-option");
  expect(adopted).toHaveTextContent("採用されています");
  expect(adopted).not.toHaveTextContent("適用されました");
  expect(adopted).not.toHaveTextContent("デプロイされました");
  expect(adopted).not.toHaveTextContent("マージされました");
  expect(adopted.textContent).toMatch(/適用.*一切行いません/);
});

// --- no composite score -------------------------------------------------------

test("画面のどこにも合成スコア・完成度・確信度・パーセンテージは出現しない", async () => {
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut()]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
    "/ux-design/journeys/checkout-to-be/baseline-diff": {
      system_id: 1, journey_id: 1, journey_key: "checkout-to-be", generated_at: 1000,
      diff_state: "not_applicable", from_revision_id: null, from_revision_number: null,
      to_revision_id: null, to_revision_number: null, steps: [], degraded_sections: [], degraded_detail: {},
    } satisfies UxJourneyDiffOut,
    "/ux-design/requirements": requirementListOut([requirementOut()]),
    "/ux-design/requirements/single-page-checkout": requirementDetailOut(),
    "/solution-designs": solutionDesignListOut([solutionDesignOut()]),
    "/solution-designs/address-autofill": solutionDesignDetailOut(),
    "/solution-designs/address-autofill/change-origins": {
      system_id: 1, solution_design_id: 20, design_key: "address-autofill", generated_at: 1000,
      origins: [], degraded_sections: [], degraded_detail: {},
    },
    "/solution-designs/address-autofill/handoff": handoffOut(),
  });
  await renderPage();

  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));
  await screen.findByTestId("ux-journey-detail");
  expect(document.body.textContent).not.toMatch(/%/);

  fireEvent.click(screen.getByTestId("ux-design-studio-tab-requirements"));
  fireEvent.click(await screen.findByTestId("ux-requirement-item-single-page-checkout"));
  await screen.findByTestId("ux-requirement-detail");

  fireEvent.click(screen.getByTestId("ux-design-studio-tab-solutions"));
  fireEvent.click(await screen.findByTestId("ux-solution-design-item-address-autofill"));
  await screen.findByTestId("ux-solution-design-handoff");

  const bodyText = document.body.textContent ?? "";
  expect(bodyText).not.toMatch(/%/);
  expect(bodyText).not.toMatch(/スコア/);
  expect(bodyText).not.toMatch(/完成度/);
  expect(bodyText).not.toMatch(/確信度/);
});

// --- grouped evaluation policy refs ------------------------------------------

test("評価方針は level ごとに独立したグループとして表示され、1 つのリストに混ざらない", async () => {
  mockGet({
    "/solution-designs": solutionDesignListOut([solutionDesignOut()]),
    "/solution-designs/address-autofill": solutionDesignDetailOut(),
    "/solution-designs/address-autofill/change-origins": {
      system_id: 1, solution_design_id: 20, design_key: "address-autofill", generated_at: 1000,
      origins: [], degraded_sections: [], degraded_detail: {},
    },
    "/solution-designs/address-autofill/handoff": handoffOut(),
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-design-studio-tab-solutions"));
  fireEvent.click(await screen.findByTestId("ux-solution-design-item-address-autofill"));
  await screen.findByTestId("ux-solution-design-handoff");

  const nodeGroup = screen.getByTestId("ux-solution-design-evaluation-node");
  const flowGroup = screen.getByTestId("ux-solution-design-evaluation-flow_capability");
  const uxGroup = screen.getByTestId("ux-solution-design-evaluation-ux_outcome");

  expect(within(nodeGroup).getByText(/node-p1/)).toBeInTheDocument();
  expect(within(flowGroup).queryByText(/node-p1/)).not.toBeInTheDocument();
  expect(within(uxGroup).queryByText(/node-p1/)).not.toBeInTheDocument();
  expect(within(uxGroup).getByText(/ux-p1/)).toBeInTheDocument();
  // The empty level still renders as its OWN group, never dropped.
  expect(within(flowGroup).getByTestId("ux-solution-design-evaluation-flow_capability-empty")).toBeInTheDocument();
});

// --- failed query renders an error card, never an empty page body -----------

test("失敗したクエリはエラーカードと再試行を表示し、空のページにはならない", async () => {
  mockGet({ "/ux-design/journeys": new Error("network down") });
  await renderPage();

  const errorCard = await screen.findByTestId("ux-journey-list-error");
  expect(errorCard).toBeInTheDocument();
  expect(document.body.textContent?.trim().length ?? 0).toBeGreaterThan(0);

  mockApi.get.mockClear();
  mockGet({ "/ux-design/journeys": journeyListOut([journeyOut()]) });
  fireEvent.click(within(errorCard).getByRole("button", { name: "再試行" }));
  await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/ux-design/journeys"));
  await screen.findByTestId("ux-journey-list");
});

// --- structural join: requirements linked to a step --------------------------

test("Step に紐づく Requirement はサーバーの step_links から結合表示され、superseded なリンクは除外される", async () => {
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut()]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
    "/ux-design/requirements": requirementListOut([
      requirementOut({ requirement_key: "single-page-checkout", id: 10 }),
      requirementOut({ requirement_key: "old-address-flow", id: 11 }),
    ]),
    "/ux-design/requirements/single-page-checkout": requirementDetailOut(),
    "/ux-design/requirements/old-address-flow": requirementDetailOut({
      id: 11, requirement_key: "old-address-flow",
      step_links: [
        {
          id: 2, requirement_id: 11, journey_id: 1, journey_key: "checkout-to-be",
          step_key: "enter-address", step_label: null, captured_journey_revision_id: 1,
          captured_step_digest: "step-digest-1", target_resolution: "resolved",
          recheck_state: "current", note: "", decision_method: "manual",
          created_by: "dev", created_at: 900, superseded_by_id: 3, // superseded -> excluded
        },
      ],
    }),
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));
  fireEvent.click(await screen.findByTestId("ux-journey-step-item-enter-address"));

  const linked = await screen.findByTestId("ux-journey-step-requirements");
  expect(within(linked).getByText("single-page-checkout")).toBeInTheDocument();
  expect(within(linked).queryByText("old-address-flow")).not.toBeInTheDocument();
});

// --- navigation: step -> requirement -> solution design ----------------------

test("Step のリンクから Requirement タブへ、そこから Solution Design タブへ遷移できる(実行はしない)", async () => {
  mockGet({
    "/ux-design/journeys": journeyListOut([journeyOut()]),
    "/ux-design/journeys/checkout-to-be": journeyDetailOut(),
    "/ux-design/requirements": requirementListOut([requirementOut()]),
    "/ux-design/requirements/single-page-checkout": requirementDetailOut(),
    "/solution-designs": solutionDesignListOut([solutionDesignOut()]),
    "/solution-designs/address-autofill": solutionDesignDetailOut(),
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-journey-item-checkout-to-be"));
  fireEvent.click(await screen.findByTestId("ux-journey-step-item-enter-address"));
  fireEvent.click(await screen.findByTestId("ux-journey-step-requirement-link-single-page-checkout"));

  await screen.findByTestId("ux-requirement-detail");
  expect(screen.getByTestId("ux-design-studio-panel-requirements")).toBeInTheDocument();

  fireEvent.click(await screen.findByTestId("ux-requirement-solution-design-link-address-autofill"));
  await screen.findByTestId("ux-solution-design-detail");
  expect(screen.getByTestId("ux-design-studio-panel-solutions")).toBeInTheDocument();
});

// --- out-of-scope requirements cannot take a target link ---------------------

test("out_of_scope の Requirement は実装対象に接続できない旨が案内される", async () => {
  mockGet({
    "/ux-design/requirements": requirementListOut([
      requirementOut({ requirement_key: "wont-do", requirement_kind: "out_of_scope" }),
    ]),
    "/ux-design/requirements/wont-do": requirementDetailOut({
      requirement_key: "wont-do", requirement_kind: "out_of_scope", step_links: [],
    }),
  });
  await renderPage();
  fireEvent.click(await screen.findByTestId("ux-design-studio-tab-requirements"));
  fireEvent.click(await screen.findByTestId("ux-requirement-item-wont-do"));

  expect(await screen.findByTestId("ux-requirement-out-of-scope-note")).toHaveTextContent(
    "実装対象へは接続できません",
  );
});

// --- model.ts: pure functions, unit-tested directly (no React, no API) ------

describe("components/ux-design/model.ts (pure functions)", () => {
  test("sortJourneys: as_is が to_be より先、同じ perspective では journey_key 順", async () => {
    const { sortJourneys } = await import("@/components/ux-design/model");
    const sorted = sortJourneys([
      journeyOut({ journey_key: "z-to-be", perspective: "to_be", id: 1 }),
      journeyOut({ journey_key: "a-as-is", perspective: "as_is", id: 2 }),
      journeyOut({ journey_key: "b-as-is", perspective: "as_is", id: 3 }),
    ]);
    expect(sorted.map((j) => j.journey_key)).toEqual(["a-as-is", "b-as-is", "z-to-be"]);
  });

  test("sortRequirements: functional/non_functional/constraint/out_of_scope の順", async () => {
    const { sortRequirements } = await import("@/components/ux-design/model");
    const sorted = sortRequirements([
      requirementOut({ requirement_key: "c1", requirement_kind: "constraint", id: 1 }),
      requirementOut({ requirement_key: "f1", requirement_kind: "functional", id: 2 }),
      requirementOut({ requirement_key: "o1", requirement_kind: "out_of_scope", id: 3 }),
      requirementOut({ requirement_key: "n1", requirement_kind: "non_functional", id: 4 }),
    ]);
    expect(sorted.map((r) => r.requirement_key)).toEqual(["f1", "n1", "c1", "o1"]);
  });

  test("evaluationPolicyGroups: 3 level を固定順で分離し、空の level も消さない", async () => {
    const { evaluationPolicyGroups } = await import("@/components/ux-design/model");
    const groups = evaluationPolicyGroups({ ux_outcome: [{ a: 1 }], node: [] });
    expect(groups.map((g) => g.level)).toEqual(["node", "flow_capability", "ux_outcome"]);
    expect(groups[0].items).toEqual([]);
    expect(groups[1].items).toEqual([]); // flow_capability absent entirely from input -> still present, empty
    expect(groups[2].items).toEqual([{ a: 1 }]);
  });

  test("baselineDiffUnavailableMessage: greenfield と undecided は別の文言で、どちらも「差分なし」ではない", async () => {
    const { baselineDiffUnavailableMessage } = await import("@/components/ux-design/model");
    const greenfield = baselineDiffUnavailableMessage("greenfield");
    const undecided = baselineDiffUnavailableMessage("undecided");
    expect(greenfield).not.toBe(undecided);
    expect(greenfield).not.toContain("差分なし");
    expect(undecided).not.toContain("差分なし");
  });

  test("requirementsForStep: journey_id と step_key の完全一致のみを結合し、superseded を除外する", async () => {
    const { requirementsForStep } = await import("@/components/ux-design/model");
    const matching = requirementDetailOut({ id: 1, requirement_key: "match" });
    const wrongStep = requirementDetailOut({
      id: 2, requirement_key: "wrong-step",
      step_links: [{ ...matching.step_links[0], step_key: "other-step", superseded_by_id: null }],
    });
    const superseded = requirementDetailOut({
      id: 3, requirement_key: "superseded",
      step_links: [{ ...matching.step_links[0], superseded_by_id: 999 }],
    });
    const result = requirementsForStep([matching, wrongStep, superseded], 1, "enter-address");
    expect(result.map((r) => r.requirement.requirement_key)).toEqual(["match"]);
  });

  test("solutionDesignsForRequirement: requirement_id の完全一致のみを結合し、superseded を除外する", async () => {
    const { solutionDesignsForRequirement } = await import("@/components/ux-design/model");
    const design = solutionDesignDetailOut({ id: 1, design_key: "d1" });
    const unrelated = solutionDesignDetailOut({
      id: 2, design_key: "d2",
      requirement_links: [{ ...design.requirement_links[0], requirement_id: 999 }],
    });
    const supersededLink = solutionDesignDetailOut({
      id: 3, design_key: "d3",
      requirement_links: [{ ...design.requirement_links[0], superseded_by_id: 999 }],
    });
    const result = solutionDesignsForRequirement([design, unrelated, supersededLink], 10);
    expect(result.map((r) => r.design.design_key)).toEqual(["d1"]);
  });

  test("targetLinksForAdoptedOption: adopted_option_key と一致する option の current な target_links のみ返す", async () => {
    const { targetLinksForAdoptedOption } = await import("@/components/ux-design/model");
    const design = solutionDesignDetailOut();
    const result = targetLinksForAdoptedOption(design);
    expect(result?.optionKey).toBe("opt-a");
    expect(result?.links).toHaveLength(1);

    const noneAdopted = solutionDesignDetailOut({ adopted_option_key: null });
    expect(targetLinksForAdoptedOption(noneAdopted)).toBeNull();
  });

  test("requirementAcceptsTargetLink: out_of_scope のみ false", async () => {
    const { requirementAcceptsTargetLink } = await import("@/components/ux-design/model");
    expect(requirementAcceptsTargetLink(requirementOut({ requirement_kind: "functional" }))).toBe(true);
    expect(requirementAcceptsTargetLink(requirementOut({ requirement_kind: "out_of_scope" }))).toBe(false);
  });
});
