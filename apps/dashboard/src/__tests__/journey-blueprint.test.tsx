/// <reference types="vitest/globals" />
// Issue #423 (Epic #418): Journey Service Blueprint Dashboard tests.
//
// `docs/stakeholder-value-network.md` §0 invariant 9 is what every test here
// protects: the client re-derives no lane state, no diff, no staleness --
// it only renders what `GET /journey-blueprint` / `.../diff` already
// decided. Pure classification/grouping is unit-tested directly against
// `model.ts` at the bottom of this file.

import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type {
  BlueprintDiffOut, BlueprintLaneCellOut, BlueprintOut, BlueprintStepOut, UxJourneyListOut, UxJourneyOut,
} from "@/api/types";
import { diffChangeGroups, orderedLanes, orderedSteps } from "@/components/journey-blueprint/model";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return (
    <MemoryRouter initialEntries={["/journey-blueprint"]}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

async function renderPage() {
  const { default: JourneyBlueprintPage } = await import("@/pages/journey-blueprint");
  return render(<JourneyBlueprintPage />, { wrapper });
}

// --- fixtures ---------------------------------------------------------------

function journeyOut(overrides: Partial<UxJourneyOut> = {}): UxJourneyOut {
  return {
    id: 1, system_id: 1, journey_key: "checkout-to-be", perspective: "to_be",
    baseline_mode: "undecided", baseline_journey_id: null, baseline_journey_key: null,
    baseline_state: "absent", current_revision_id: 1, current_revision_number: 1,
    title: "チェックアウトの目標像", design_status: "proposed", recheck_state: "current",
    created_by: "dev", created_at: 1000, updated_at: 1000, ...overrides,
  };
}

function journeyListOut(journeys: UxJourneyOut[]): UxJourneyListOut {
  return { system_id: 1, generated_at: 1000, journeys, degraded_sections: [], degraded_detail: {} };
}

function laneCell(overrides: Partial<BlueprintLaneCellOut> = {}): BlueprintLaneCellOut {
  return {
    lane_kind: "stakeholder_action", state: "unknown", summary: "",
    stakeholder_links: [], delivery_links: [], exchange_links: [], requirement_refs: [], evidence_refs: [],
    ...overrides,
  };
}

const ALL_LANE_KINDS = [
  "stakeholder_action", "touchpoint", "frontstage", "backstage",
  "support", "external", "requirement", "evidence", "failure_recovery",
] as const;

function fullLanes(overrides: Record<string, BlueprintLaneCellOut> = {}): Record<string, BlueprintLaneCellOut> {
  const lanes: Record<string, BlueprintLaneCellOut> = {};
  for (const kind of ALL_LANE_KINDS) {
    lanes[kind] = laneCell({ lane_kind: kind as BlueprintLaneCellOut["lane_kind"] });
  }
  return { ...lanes, ...overrides };
}

function stepOut(overrides: Partial<BlueprintStepOut> = {}): BlueprintStepOut {
  return {
    step_key: "s1", step_order: 0, user_intent: "商品を選ぶ", system_response: "在庫を確認する",
    lanes: fullLanes(), ...overrides,
  };
}

function blueprintOut(overrides: Partial<BlueprintOut> = {}): BlueprintOut {
  return {
    journey_key: "checkout-to-be", perspective: "to_be", baseline_state: "absent",
    current_revision_number: 1, steps: [stepOut()], degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function diffOut(overrides: Partial<BlueprintDiffOut> = {}): BlueprintDiffOut {
  return {
    journey_key: "checkout-to-be", diff_state: "available", from_revision_number: 1, to_revision_number: 2,
    steps: [], degraded_sections: [], degraded_detail: {}, ...overrides,
  };
}

function mockGet(overrides: Record<string, unknown>) {
  mockApi.get.mockImplementation((path: string) => {
    if (path in overrides) {
      const v = overrides[path];
      return v instanceof Error ? Promise.reject(v) : Promise.resolve(v);
    }
    if (path === "/ux-design/journeys") return Promise.resolve(journeyListOut([]));
    return new Promise(() => {});
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

describe("Journey Service Blueprint の表示", () => {
  test("Journey 未選択のときは案内文のみ表示する", async () => {
    mockGet({ "/ux-design/journeys": journeyListOut([]) });
    await renderPage();
    expect(await screen.findByTestId("blueprint-no-journey")).toBeInTheDocument();
  });

  test("Journey を選ぶと 9 レーン全てが表示される", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut(),
    });
    await renderPage();

    for (const kind of ALL_LANE_KINDS) {
      expect(await screen.findByTestId(`blueprint-cell-${kind}`)).toBeInTheDocument();
    }
  });

  test("セルを選択すると詳細ペインに内容が表示される", async () => {
    const journey = journeyOut();
    const bp = blueprintOut({
      steps: [
        stepOut({
          lanes: fullLanes({
            stakeholder_action: laneCell({
              lane_kind: "stakeholder_action",
              state: "present",
              summary: "商品を選ぶ",
              stakeholder_links: [
                {
                  id: 1, journey_id: 1, journey_key: "checkout-to-be", step_key: "s1", step_label: "商品を選ぶ",
                  stakeholder_key: "buyer", stakeholder_name: "購入責任者", role: "payer",
                  target_resolution: "resolved", recheck_state: "current", note: "", decision_method: "manual",
                  created_by: "dev", created_at: 1000, superseded_by_id: null,
                },
              ],
            }),
          }),
        }),
      ],
    });
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": bp,
    });
    await renderPage();

    fireEvent.click(await screen.findByTestId("blueprint-cell-stakeholder_action"));
    expect(await screen.findByTestId("blueprint-detail-pane")).toBeInTheDocument();
    expect(await screen.findByTestId("blueprint-detail-stakeholder-link")).toHaveTextContent("購入責任者");
  });

  test("unknown / not_applicable / unavailable の 3 状態がそれぞれ別表示になる", async () => {
    const journey = journeyOut();
    const bp = blueprintOut({
      steps: [
        stepOut({
          lanes: fullLanes({
            requirement: laneCell({ lane_kind: "requirement", state: "unknown" }),
            backstage: laneCell({ lane_kind: "backstage", state: "not_applicable" }),
            evidence: laneCell({ lane_kind: "evidence", state: "unavailable" }),
          }),
        }),
      ],
    });
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": bp,
    });
    await renderPage();

    expect(await screen.findByTestId("blueprint-cell-state-requirement")).toHaveTextContent("未記録");
    expect(await screen.findByTestId("blueprint-cell-state-backstage")).toHaveTextContent("対象外");
    expect(await screen.findByTestId("blueprint-cell-state-evidence")).toHaveTextContent("取得できませんでした");
  });

  test("degraded_sections がある場合は警告文を表示する", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut({ degraded_sections: ["requirement_refs"] }),
    });
    await renderPage();
    expect(await screen.findByTestId("blueprint-degraded")).toHaveTextContent("requirement_refs");
  });

  test("取得エラー時はエラー表示になる", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": new Error("boom"),
    });
    await renderPage();
    expect(await screen.findByTestId("blueprint-error")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// as-is / to-be switch
// ---------------------------------------------------------------------------

describe("as-is / to-be の切り替え", () => {
  test("差分ビューに切り替えると diff を取得して表示する", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut(),
      "/journey-blueprint/diff?journey_key=checkout-to-be": diffOut({
        steps: [
          {
            step_key: "s1", change_kind: "added", from_step_order: null, to_step_order: 0,
            from_content_digest: null, to_content_digest: "d1", from_user_intent: null, to_user_intent: "new",
          },
        ],
      }),
    });
    await renderPage();

    fireEvent.click(await screen.findByTestId("blueprint-view-diff"));
    expect(await screen.findByTestId("blueprint-diff-panel")).toBeInTheDocument();
    expect(await screen.findByTestId("blueprint-diff-group-added")).toHaveTextContent("1 件");
  });

  test("baseline が linked でない場合は not_applicable の文言を表示する", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut(),
      "/journey-blueprint/diff?journey_key=checkout-to-be": diffOut({ diff_state: "not_applicable", steps: [] }),
    });
    await renderPage();

    fireEvent.click(await screen.findByTestId("blueprint-view-diff"));
    expect(await screen.findByTestId("blueprint-diff-not-available")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Drill-down deep links
// ---------------------------------------------------------------------------

describe("Requirement への drill-down", () => {
  test("Requirement 参照を開くリンクが押せる", async () => {
    const journey = journeyOut();
    const bp = blueprintOut({
      steps: [
        stepOut({
          lanes: fullLanes({
            requirement: laneCell({
              lane_kind: "requirement",
              state: "present",
              requirement_refs: [
                { requirement_key: "req-checkout", statement: "決済を完了できる", target_resolution: "resolved", design_status: "confirmed" },
              ],
            }),
          }),
        }),
      ],
    });
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": bp,
    });
    await renderPage();

    fireEvent.click(await screen.findByTestId("blueprint-cell-requirement"));
    const openButton = await screen.findByTestId("blueprint-detail-open-requirement");
    expect(openButton).toBeInTheDocument();
    // Clicking it must not throw -- it only calls the page's own callback.
    fireEvent.click(openButton);
  });
});

// ---------------------------------------------------------------------------
// Narrow width: nothing hidden, grid stays reachable via horizontal scroll
// ---------------------------------------------------------------------------

describe("狭い画面幅でも状態を隠さない", () => {
  test("グリッドは overflow-x-auto でスクロール可能なまま全レーンを保持する", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut(),
    });
    await renderPage();

    const grid = await screen.findByTestId("blueprint-grid");
    expect(grid.className).toContain("overflow-x-auto");
    for (const kind of ALL_LANE_KINDS) {
      expect(await screen.findByTestId(`blueprint-cell-${kind}`)).toBeInTheDocument();
    }
  });
});

// ---------------------------------------------------------------------------
// Pure model.ts unit tests
// ---------------------------------------------------------------------------

describe("model.ts の純粋関数", () => {
  test("orderedLanes は固定順で 9 レーンを返す", () => {
    const lanes = fullLanes();
    const ordered = orderedLanes(lanes);
    expect(ordered.map((c) => c.lane_kind)).toEqual([...ALL_LANE_KINDS]);
  });

  test("orderedSteps は step_order でソートする", () => {
    const bp = blueprintOut({
      steps: [stepOut({ step_key: "s2", step_order: 1 }), stepOut({ step_key: "s1", step_order: 0 })],
    });
    expect(orderedSteps(bp).map((s) => s.step_key)).toEqual(["s1", "s2"]);
  });

  test("diffChangeGroups は空のグループも保持する", () => {
    const groups = diffChangeGroups(diffOut({ steps: [] }));
    expect(groups.map((g) => g.changeKind)).toEqual(["added", "removed", "changed", "reordered", "unchanged"]);
    expect(groups.every((g) => g.entries.length === 0)).toBe(true);
  });

  test("diffChangeGroups は reordered と changed を混同しない", () => {
    const groups = diffChangeGroups(
      diffOut({
        steps: [
          { step_key: "a", change_kind: "reordered", from_step_order: 0, to_step_order: 1, from_content_digest: "d", to_content_digest: "d", from_user_intent: "x", to_user_intent: "x" },
          { step_key: "b", change_kind: "changed", from_step_order: 0, to_step_order: 0, from_content_digest: "d1", to_content_digest: "d2", from_user_intent: "x", to_user_intent: "y" },
        ],
      }),
    );
    const reordered = groups.find((g) => g.changeKind === "reordered")!;
    const changed = groups.find((g) => g.changeKind === "changed")!;
    expect(reordered.entries.map((e) => e.step_key)).toEqual(["a"]);
    expect(changed.entries.map((e) => e.step_key)).toEqual(["b"]);
  });
});

// ---------------------------------------------------------------------------
// Journey の自動選択と明示的な選択解除
// ---------------------------------------------------------------------------

describe("Journey の自動選択", () => {
  test("最初の Journey を自動選択する", async () => {
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut(),
    });
    await renderPage();

    const select = (await screen.findByTestId("blueprint-journey-select")) as HTMLSelectElement;
    expect(select.value).toBe("checkout-to-be");
  });

  test("明示的に選択を解除したら自動選択で上書きしない", async () => {
    // 自動選択は「開発者がまだ選んでいない間」だけ働く。解除を上書きすると
    // 未選択状態へ到達できなくなる(#349 / #356 が Interview 画面で直した
    // 欠陥と同じ)。この振る舞いは render 中の導出で成立しており、effect 内の
    // setState には依存しない。
    const journey = journeyOut();
    mockGet({
      "/ux-design/journeys": journeyListOut([journey]),
      "/journey-blueprint?journey_key=checkout-to-be": blueprintOut(),
    });
    await renderPage();

    const select = (await screen.findByTestId("blueprint-journey-select")) as HTMLSelectElement;
    expect(select.value).toBe("checkout-to-be");

    fireEvent.change(select, { target: { value: "" } });

    expect(await screen.findByTestId("blueprint-no-journey")).toBeInTheDocument();
    expect((screen.getByTestId("blueprint-journey-select") as HTMLSelectElement).value).toBe("");
  });
});
