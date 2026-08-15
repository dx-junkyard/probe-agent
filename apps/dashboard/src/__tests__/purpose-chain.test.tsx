/// <reference types="vitest/globals" />
// Issue #390: the Purpose Chain's Overview Level 0 card and Interview
// Level 1 panel.
//
// Everything semantic is decided server-side (`GET /purpose-chain`,
// `GET /purpose-chain/next-question`); these tests assert the Dashboard
// renders the server's conclusions without losing, conflating, or silently
// re-deciding any of them, and cover the 12 states `docs/purpose-chain.md`
// §3.3 lists.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import {
  additionalInterventionElements,
  capabilityElements,
  capabilityRelation,
  frameRelation,
  frameSlots,
  questionAwaitsHumanDecision,
  questionTargets,
  relationIsDecidable,
} from "@/components/purpose-chain/model";
import { PurposeFrameCard, purposeInterviewHref } from "@/components/purpose-chain/purpose-frame-card";
// `PurposeFramePanel` calls the `@/api/hooks` queries/mutations directly, so
// (unlike the props-only `PurposeFrameCard`) it must be imported dynamically
// AFTER `@/api/client` is mocked below -- a static top-level `import` is
// hoisted by the ES module spec ahead of everything else in this file,
// including the `mockApi` this mock factory closes over.
import type {
  OverviewOut, PurposeChainOut, PurposeElementOut, PurposeQuestionOut, PurposeRelationOut,
} from "@/api/types";

// ── fixtures ─────────────────────────────────────────────────────────────

function element(overrides: Partial<PurposeElementOut> & { id: string; kind: PurposeElementOut["kind"] }): PurposeElementOut {
  return {
    state: "present",
    display_statement: "",
    statement: "",
    confirmation: "ai_hypothesis",
    confirmation_label: "AI 仮説・未確認",
    provenance: "ai_hypothesis",
    provenance_label: "AI の解釈・仮説",
    resolution_level: "L0",
    source_kind: "none",
    source_ids: [],
    intent_revision_id: null,
    understanding_revision_id: null,
    snapshot_id: null,
    evidence: [],
    evidence_stale: false,
    missing_information: [],
    is_mock: false,
    ...overrides,
  };
}

function relation(overrides: Partial<PurposeRelationOut> & { id: string; kind: PurposeRelationOut["kind"]; source_id: string; target_id: string; status: PurposeRelationOut["status"] }): PurposeRelationOut {
  return {
    status_label: "",
    recheck_state: "current",
    stale_reason: null,
    provenance: "ai_hypothesis",
    provenance_label: "AI の解釈・仮説",
    decision_id: null,
    decided_at: null,
    decided_by: null,
    rationale: "",
    evidence: [],
    ...overrides,
  };
}

function chain(overrides: Partial<PurposeChainOut> = {}): PurposeChainOut {
  const beneficiary = element({
    id: "beneficiary_problem", kind: "beneficiary_problem", state: "unknown",
  });
  const desired = element({ id: "desired_change", kind: "desired_change", state: "unknown" });
  const intervention = element({ id: "intervention", kind: "intervention", state: "unknown" });
  return {
    system_id: 1,
    session_id: 7,
    generated_at: 1000,
    frame: { beneficiary_problem: beneficiary, desired_change: desired, intervention },
    elements: [beneficiary, desired, intervention],
    relations: [],
    frame_resolution_level: "L0",
    frame_state: "empty",
    snapshot_id: null,
    understanding_revision_id: null,
    understanding_confirmed_at: null,
    degraded_sections: [],
    degraded_detail: {},
    ...overrides,
  };
}

function question(overrides: Partial<PurposeQuestionOut> = {}): PurposeQuestionOut {
  return {
    need_id: "frame_missing:desired_change",
    need_code: "frame_missing",
    rule_row: 2,
    prompt: "望ましい変化は何ですか？",
    why_now: "Purpose Frame の中心的な要素がまだありません。",
    blocked_decision: "この理解で進めるかの判断",
    unlocks: "Vision の確認が進みます",
    defer_impact: "この判断は保留されたままになります",
    target_kind: "element",
    target_id: "desired_change",
    target_label: "望ましい変化",
    answerability: "human_judgement",
    suggested_answer: null,
    state: "available",
    source_revision_ids: [],
    fallback_reason: null,
    routed_needs: [],
    ...overrides,
  };
}

function overview(overrides: Partial<OverviewOut> = {}): OverviewOut {
  return {
    system_id: 1,
    generated_at: 1000,
    interview_session_id: 7,
    brief: null,
    snapshot_id: null,
    snapshot_commit_sha: null,
    latest_ready_snapshot_id: null,
    snapshot_freshness: "unavailable",
    understanding_revision_id: null,
    understanding_confirmed_at: null,
    findings: [],
    findings_initial_count: 0,
    findings_state: "not_compared",
    findings_baseline_state: "no_baseline",
    findings_baseline_label: "",
    findings_baseline_at: null,
    next_action: null,
    next_action_state: "unavailable",
    next_action_message: "",
    loop_stages: [],
    user_phase: "setup",
    runtime: null,
    degraded_sections: [],
    degraded_detail: {},
    purpose_chain: chain(),
    ...overrides,
  };
}

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

// ── model.ts (pure) ─────────────────────────────────────────────────────

describe("purpose-chain/model.ts", () => {
  test("frameSlots returns the 3 frame elements in causal order", () => {
    const c = chain();
    const slots = frameSlots(c);
    expect(slots.map((s) => s.kind)).toEqual(["beneficiary_problem", "desired_change", "intervention"]);
  });

  test("frameSlots reports null for a degraded frame section without guessing", () => {
    const c = chain({
      frame: { beneficiary_problem: null, desired_change: null, intervention: null },
      elements: [],
      frame_state: "unavailable",
      degraded_sections: ["frame"],
    });
    expect(frameSlots(c).every((s) => s.element === null)).toBe(true);
  });

  test("additionalInterventionElements excludes the frame slot itself", () => {
    const frameIntervention = element({ id: "intervention", kind: "intervention", state: "present" });
    const extra = element({ id: "intervention:abc", kind: "intervention", state: "present" });
    const c = chain({
      frame: { beneficiary_problem: null, desired_change: null, intervention: frameIntervention },
      elements: [frameIntervention, extra],
    });
    expect(additionalInterventionElements(c)).toEqual([extra]);
  });

  test("capabilityElements filters by kind only", () => {
    const cap = element({ id: "core_capability:1", kind: "core_capability" });
    const c = chain({ elements: [...chain().elements, cap] });
    expect(capabilityElements(c)).toEqual([cap]);
  });

  test("frameRelation / capabilityRelation look relations up by kind and target, unmodified", () => {
    const r1 = relation({
      id: "problem_to_change:beneficiary_problem->desired_change",
      kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change",
      status: "hypothesis",
    });
    const r2 = relation({
      id: "intervention_to_capability:intervention->core_capability:1",
      kind: "intervention_to_capability", source_id: "intervention", target_id: "core_capability:1",
      status: "confirmed",
    });
    const c = chain({ relations: [r1, r2] });
    expect(frameRelation(c, "problem_to_change")).toBe(r1);
    expect(capabilityRelation(c, "core_capability:1")).toBe(r2);
    expect(capabilityRelation(c, "nope")).toBeUndefined();
  });

  test.each([
    ["confirmed", true], ["hypothesis", true], ["conflicting", true],
    ["unknown", false], ["unavailable", false],
  ] as const)("relationIsDecidable(%s) -> %s", (status, expected) => {
    expect(relationIsDecidable(relation({
      id: "x", kind: "problem_to_change", source_id: "a", target_id: "b", status,
    }))).toBe(expected);
  });

  test("questionTargets matches on kind AND id only", () => {
    const q = question({ target_kind: "element", target_id: "desired_change" });
    expect(questionTargets(q, "element", "desired_change")).toBe(true);
    expect(questionTargets(q, "element", "intervention")).toBe(false);
    expect(questionTargets(q, "relation", "desired_change")).toBe(false);
    expect(questionTargets(null, "element", "desired_change")).toBe(false);
  });

  test("questionAwaitsHumanDecision requires human_judgement AND available", () => {
    expect(questionAwaitsHumanDecision(question({ answerability: "human_judgement", state: "available" }))).toBe(true);
    expect(questionAwaitsHumanDecision(question({ answerability: "system_researchable", state: "available" }))).toBe(false);
    expect(questionAwaitsHumanDecision(question({ answerability: "human_judgement", state: "deferred" }))).toBe(false);
  });
});

// ── Overview Level 0: PurposeFrameCard ────────────────────────────────────

describe("PurposeFrameCard (Overview Level 0, §3.1)", () => {
  // State 1: 3要素すべて unknown
  test("state 1: all 3 elements unknown render as distinct 'not yet known' notes, never a fabricated statement", () => {
    wrap(<PurposeFrameCard overview={overview()} question={null} questionState="ready" />);
    const card = screen.getByTestId("overview-purpose-frame");
    for (const kind of ["beneficiary_problem", "desired_change", "intervention"]) {
      const li = within(card).getByTestId(`overview-purpose-element-${kind}`);
      expect(li).toHaveTextContent("まだわかっていません");
    }
  });

  // State 2: AI 候補のみ (present, but ai_hypothesis / not confirmed)
  test("state 2: AI-hypothesis-only elements show the statement with an explicit unconfirmed badge", () => {
    const c = chain({
      frame: {
        beneficiary_problem: element({
          id: "beneficiary_problem", kind: "beneficiary_problem", state: "present",
          display_statement: "既存顧客の手戻り", confirmation: "ai_hypothesis",
        }),
        desired_change: element({
          id: "desired_change", kind: "desired_change", state: "present",
          display_statement: "手戻りを減らす", confirmation: "ai_hypothesis",
        }),
        intervention: element({
          id: "intervention", kind: "intervention", state: "present",
          display_statement: "自動チェックを行う", confirmation: "ai_hypothesis",
        }),
      },
      frame_state: "complete",
    });
    wrap(<PurposeFrameCard overview={overview({ purpose_chain: c })} question={null} questionState="ready" />);
    const beneficiary = screen.getByTestId("overview-purpose-element-beneficiary_problem");
    expect(beneficiary).toHaveTextContent("既存顧客の手戻り");
    expect(beneficiary).toHaveTextContent("AI 仮説・未確認");
  });

  // State 3: Vision confirmed, Purpose hypothesis (mixed confirmation)
  test("state 3: a confirmed element shows no unconfirmed badge while a sibling hypothesis element does", () => {
    const c = chain({
      frame: {
        beneficiary_problem: element({ id: "beneficiary_problem", kind: "beneficiary_problem", state: "present", display_statement: "課題", confirmation: "confirmed" }),
        desired_change: element({ id: "desired_change", kind: "desired_change", state: "present", display_statement: "変化", confirmation: "confirmed" }),
        intervention: element({ id: "intervention", kind: "intervention", state: "present", display_statement: "介入", confirmation: "ai_hypothesis" }),
      },
      frame_state: "complete",
    });
    wrap(<PurposeFrameCard overview={overview({ purpose_chain: c })} question={null} questionState="ready" />);
    const change = screen.getByTestId("overview-purpose-element-desired_change");
    expect(within(change).queryByText("AI 仮説・未確認")).not.toBeInTheDocument();
    const intervention = screen.getByTestId("overview-purpose-element-intervention");
    expect(within(intervention).getByText("AI 仮説・未確認")).toBeInTheDocument();
  });

  // State 7: 質問不要
  test("state 7: no active question renders a positive 'nothing to confirm' note, not silence", () => {
    wrap(<PurposeFrameCard overview={overview()} question={null} questionState="ready" />);
    expect(screen.getByTestId("overview-purpose-question-none")).toHaveTextContent("追加の確認は必要ありません");
    expect(screen.queryByTestId("overview-purpose-question")).not.toBeInTheDocument();
  });

  // State 8: 質問1件 — CTA navigates, never executes (#358)
  test("state 8: exactly one question renders with why_now/unlocks and a navigating CTA deep link", () => {
    const q = question({ need_id: "frame_missing:desired_change" });
    wrap(<PurposeFrameCard overview={overview({ interview_session_id: 7 })} question={q} questionState="ready" />);
    const box = screen.getByTestId("overview-purpose-question");
    expect(box).toHaveTextContent(q.prompt);
    expect(box).toHaveTextContent(q.why_now);
    expect(box).toHaveTextContent(q.unlocks);
    const cta = screen.getByTestId("overview-purpose-question-cta");
    expect(cta.tagName).toBe("A"); // a real link -- it navigates, it does not execute
    expect(cta).toHaveAttribute("href", purposeInterviewHref(7, q.need_id));
    expect(cta).toHaveAttribute("aria-describedby", `overview-purpose-question-why-${q.need_id}`);
  });

  test("purposeInterviewHref carries the destination's own parameter names", () => {
    expect(purposeInterviewHref(7, "frame_missing:x")).toBe("/interview?session=7&purpose_need=frame_missing%3Ax");
    expect(purposeInterviewHref(null)).toBe("/interview");
  });

  // State 11: 部分 API 失敗 (loading / error are distinct, and the whole card
  // degrades separately from a per-question failure)
  test("state 11a: loading the question is a distinct render from 'no question'", () => {
    wrap(<PurposeFrameCard overview={overview()} question={null} questionState="loading" />);
    expect(screen.getByTestId("overview-purpose-question-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("overview-purpose-question-none")).not.toBeInTheDocument();
  });

  test("state 11b: a question fetch error is distinct from 'no question'", () => {
    wrap(<PurposeFrameCard overview={overview()} question={null} questionState="error" />);
    expect(screen.getByTestId("overview-purpose-question-error")).toBeInTheDocument();
    expect(screen.queryByTestId("overview-purpose-question-none")).not.toBeInTheDocument();
  });

  test("state 11c: a degraded purpose_chain section renders 'unavailable', never a fabricated empty frame", () => {
    wrap(
      <PurposeFrameCard
        overview={overview({ purpose_chain: null, degraded_sections: ["purpose_chain"] })}
        question={null}
        questionState="ready"
      />,
    );
    expect(screen.getByTestId("overview-purpose-frame-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("overview-purpose-frame")).not.toBeInTheDocument();
  });

  test("no interview session yet still shows the frame plus a start-interview note, not a crash", () => {
    wrap(
      <PurposeFrameCard
        overview={overview({ interview_session_id: null, purpose_chain: chain({ session_id: null }) })}
        question={null}
        questionState="ready"
      />,
    );
    expect(screen.getByTestId("overview-purpose-no-session")).toBeInTheDocument();
  });

  // State 12 (structural half): action/CTA regions wrap rather than assuming
  // a fixed desktop width. jsdom does not apply CSS layout, so this checks
  // the responsive utility classes are present rather than measuring pixels
  // (the same limitation `layout-navigation.test.tsx` works within).
  test("state 12: the question CTA sits inside a block that does not assume a fixed width", () => {
    wrap(<PurposeFrameCard overview={overview()} question={question()} questionState="ready" />);
    const box = screen.getByTestId("overview-purpose-question");
    expect(box.className).not.toMatch(/\bw-\[\d/);
  });
});

// ── Interview Level 1: PurposeFramePanel ──────────────────────────────────

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
let mockSystemId: number | null = 1;

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => mockSystemId,
  setSystemId: (id: number | null) => { mockSystemId = id; },
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

function panelWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function mockChainAndQuestion(c: PurposeChainOut | null, q: PurposeQuestionOut | null) {
  mockApi.get.mockImplementation((path: string) => {
    if (path.startsWith("/purpose-chain/next-question")) return Promise.resolve(q);
    if (path.startsWith("/purpose-chain")) return Promise.resolve(c);
    return Promise.resolve(null);
  });
}

async function renderPanel(props: { sessionId: number | null; initialNeedId?: string | null }) {
  const { PurposeFramePanel } = await import("@/components/purpose-chain/purpose-frame-panel");
  return render(<PurposeFramePanel {...props} />, { wrapper: panelWrapper() });
}

describe("PurposeFramePanel (Interview Level 1, §3.2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("renders nothing when no session is selected", async () => {
    const { container } = await renderPanel({ sessionId: null });
    expect(container).toBeEmptyDOMElement();
  });

  test("loading is its own render, not the unavailable card", async () => {
    mockApi.get.mockImplementation(() => new Promise(() => {}));
    await renderPanel({ sessionId: 7 });
    expect(screen.getByTestId("purpose-frame-panel-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("purpose-frame-panel-unavailable")).not.toBeInTheDocument();
  });

  test("state 11: a chain fetch failure renders 'unavailable', never an empty frame", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path.startsWith("/purpose-chain") && !path.includes("next-question")
        ? Promise.reject(new Error("boom"))
        : Promise.resolve(null),
    );
    await renderPanel({ sessionId: 7 });
    expect(await screen.findByTestId("purpose-frame-panel-unavailable")).toBeInTheDocument();
  });

  // State 4: 3要素 confirmed・relation unknown
  test("state 4: all elements confirmed but the relation itself is unknown, and cannot be decided", async () => {
    const c = chain({
      frame: {
        beneficiary_problem: element({ id: "beneficiary_problem", kind: "beneficiary_problem", state: "present", statement: "課題", confirmation: "confirmed" }),
        desired_change: element({ id: "desired_change", kind: "desired_change", state: "present", statement: "変化", confirmation: "confirmed" }),
        intervention: element({ id: "intervention", kind: "intervention", state: "present", statement: "介入", confirmation: "confirmed" }),
      },
      relations: [
        relation({ id: "r1", kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change", status: "unknown", status_label: "不明" }),
        relation({ id: "r2", kind: "change_to_intervention", source_id: "desired_change", target_id: "intervention", status: "unknown", status_label: "不明" }),
      ],
      frame_state: "complete",
    });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");
    const rel = screen.getByTestId("purpose-relation-problem_to_change");
    expect(rel).toHaveAttribute("data-relation-status", "unknown");
    expect(within(rel).queryByTestId("purpose-relation-confirm-problem_to_change")).not.toBeInTheDocument();
  });

  // State 5: relation conflict
  test("state 5: a conflicting relation is a distinct badge from unknown/hypothesis", async () => {
    const c = chain({
      relations: [
        relation({ id: "r1", kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change", status: "conflicting", status_label: "矛盾" }),
      ],
    });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    const rel = await screen.findByTestId("purpose-relation-problem_to_change");
    expect(rel).toHaveAttribute("data-relation-status", "conflicting");
    expect(rel).toHaveTextContent("矛盾");
  });

  // State 6: recheck (stale)
  test("state 6: a stale relation shows the recheck marker with its reason, independent of status", async () => {
    const c = chain({
      relations: [
        relation({
          id: "r1", kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change",
          status: "hypothesis", status_label: "仮説", recheck_state: "stale", stale_reason: "source_changed",
        }),
      ],
    });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    const marker = await screen.findByTestId("purpose-relation-recheck-problem_to_change");
    expect(marker).toHaveTextContent("再確認が必要");
    expect(marker).toHaveTextContent("起点の内容が変わりました");
  });

  test("a decidable relation can be confirmed with a rationale, and the mutation posts the right payload", async () => {
    const c = chain({
      relations: [
        relation({ id: "r1", kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change", status: "hypothesis", status_label: "仮説" }),
      ],
    });
    mockChainAndQuestion(c, null);
    mockApi.post.mockResolvedValue({ ...c.relations[0], status: "confirmed" });
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    fireEvent.click(screen.getByTestId("purpose-relation-confirm-problem_to_change"));
    fireEvent.change(screen.getByTestId("purpose-relation-rationale-problem_to_change"), {
      target: { value: "コードで確認した" },
    });
    fireEvent.click(screen.getByRole("button", { name: "確認を確定する" }));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/purpose-chain/relations/r1/decision",
      { session_id: 7, decision: "confirmed", rationale: "コードで確認した" },
    ));
  });

  // State 8/9/10 grouped: the active question, deferred, and routed-to-research
  test("state 8: the active question's actions attach to its target element, not a floating form", async () => {
    const c = chain();
    const q = question({ target_kind: "element", target_id: "desired_change" });
    mockChainAndQuestion(c, q);
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    const targetEl = screen.getByTestId("purpose-element-question-desired_change");
    expect(within(targetEl).getByTestId(`purpose-need-${q.need_id}`)).toBeInTheDocument();
    // Not attached to a different element.
    expect(screen.queryByTestId("purpose-element-question-intervention")).not.toBeInTheDocument();
  });

  // The server's respond_to_purpose_need 422s "confirm" for any element
  // target (frame_missing means state == "unknown": nothing exists yet to
  // confirm, only to create) -- an element-target question therefore never
  // offers a confirm button, only 内容を入力する (correct) / defer / unknown.
  test("state 8: an element-target question offers no confirm button, since the server always refuses it there", async () => {
    const c = chain();
    const q = question({ target_kind: "element", target_id: "desired_change" });
    mockChainAndQuestion(c, q);
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    const block = screen.getByTestId(`purpose-need-${q.need_id}`);
    expect(within(block).queryByTestId(`purpose-need-confirm-${q.need_id}`)).not.toBeInTheDocument();
    expect(within(block).getByTestId(`purpose-need-correct-${q.need_id}`)).toBeInTheDocument();
  });

  test("state 8: correct / unknown / defer are each a separate recorded response kind, and the request carries no rationale field", async () => {
    const c = chain();
    const q = question({ target_kind: "element", target_id: "desired_change" });
    mockChainAndQuestion(c, q);
    mockApi.post.mockResolvedValue({});
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    // `defer`
    fireEvent.click(screen.getByTestId(`purpose-need-defer-${q.need_id}`));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      `/purpose-chain/needs/${encodeURIComponent(q.need_id)}/respond`,
      { session_id: 7, response_kind: "defer", value_text: "" },
    ));

    // `correct` -- the server's `PurposeNeedRespondRequest` is
    // `extra="forbid"`: a `rationale` field here would 422 the whole call.
    fireEvent.click(screen.getByTestId(`purpose-need-correct-${q.need_id}`));
    fireEvent.change(screen.getByTestId(`purpose-need-correct-input-${q.need_id}`), {
      target: { value: "新規顧客のオンボーディング負荷" },
    });
    fireEvent.click(screen.getByTestId(`purpose-need-correct-submit-${q.need_id}`));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      `/purpose-chain/needs/${encodeURIComponent(q.need_id)}/respond`,
      { session_id: 7, response_kind: "correct", value_text: "新規顧客のオンボーディング負荷" },
    ));
  });

  // A relation-target question's confirm/correct is the SAME underlying
  // write the relation's own Confirm/Reject buttons already perform -- the
  // need panel offers only unknown/defer for it, plus a pointer to those
  // buttons, never a second form for one decision.
  test("state 8: a relation-target question points at the relation's own confirm/reject instead of duplicating a form", async () => {
    const c = chain({
      relations: [
        relation({ id: "r1", kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change", status: "hypothesis", status_label: "仮説" }),
      ],
    });
    const q = question({ need_code: "relation_unknown", target_kind: "relation", target_id: "r1" });
    mockChainAndQuestion(c, q);
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    const rel = screen.getByTestId("purpose-relation-problem_to_change");
    const questionBlock = within(rel).getByTestId(`purpose-need-${q.need_id}`);
    expect(within(questionBlock).queryByTestId(`purpose-need-correct-${q.need_id}`)).not.toBeInTheDocument();
    expect(within(questionBlock).getByTestId(`purpose-need-relation-note-${q.need_id}`)).toBeInTheDocument();
    expect(within(questionBlock).getByTestId(`purpose-need-defer-${q.need_id}`)).toBeInTheDocument();
  });

  // State 9: deferred -- a settled question is read-only, not re-offering the form
  test("state 9: a deferred question renders its state, with no correct/defer buttons", async () => {
    const c = chain();
    const q = question({ target_kind: "element", target_id: "desired_change", state: "deferred" });
    mockChainAndQuestion(c, q);
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    const block = screen.getByTestId(`purpose-need-${q.need_id}`);
    expect(within(block).getByTestId(`purpose-need-state-${q.need_id}`)).toHaveTextContent("保留中");
    expect(within(block).queryByTestId(`purpose-need-correct-${q.need_id}`)).not.toBeInTheDocument();
    expect(within(block).queryByTestId(`purpose-need-defer-${q.need_id}`)).not.toBeInTheDocument();
  });

  // State 10: system_researchable routes to investigation, never a text form
  test("state 10: a system_researchable question shows no confirm/correct form, only an explicit investigate request", async () => {
    const c = chain();
    const q = question({
      target_kind: "element", target_id: "intervention", answerability: "system_researchable",
    });
    mockChainAndQuestion(c, q);
    mockApi.post.mockResolvedValue({});
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");

    const block = screen.getByTestId(`purpose-need-${q.need_id}`);
    expect(within(block).queryByTestId(`purpose-need-confirm-${q.need_id}`)).not.toBeInTheDocument();
    expect(within(block).queryByTestId(`purpose-need-correct-${q.need_id}`)).not.toBeInTheDocument();
    expect(within(block).getByText("コード・ドキュメントから調査できます")).toBeInTheDocument();
    fireEvent.click(within(block).getByTestId(`purpose-need-investigate-${q.need_id}`));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      `/purpose-chain/needs/${encodeURIComponent(q.need_id)}/respond`,
      { session_id: 7, response_kind: "unknown", value_text: "" },
    ));
  });

  // Missing endpoints (both endpoints not yet content) render the relation
  // as un-decidable, without asserting a specific status the server did not
  // send.
  test("a relation with no data yet (undefined) shows the structural placeholder, not a crash", async () => {
    const c = chain({ relations: [] });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    await screen.findByTestId("purpose-frame-panel");
    expect(screen.getAllByTestId("purpose-relation-missing").length).toBeGreaterThan(0);
  });

  test("evidence_stale renders its own marker, independent of confirmation state", async () => {
    const c = chain({
      frame: {
        beneficiary_problem: chain().frame.beneficiary_problem,
        desired_change: chain().frame.desired_change,
        intervention: element({
          id: "intervention", kind: "intervention", state: "present", statement: "介入",
          confirmation: "confirmed", provenance: "implementation_fact", evidence_stale: true,
        }),
      },
    });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    expect(await screen.findByTestId("purpose-element-stale-intervention")).toBeInTheDocument();
  });

  // State 12 (structural half, Interview side)
  test("state 12: relation and element action rows wrap rather than assuming fixed width", async () => {
    const c = chain({
      relations: [
        relation({ id: "r1", kind: "problem_to_change", source_id: "beneficiary_problem", target_id: "desired_change", status: "hypothesis", status_label: "仮説" }),
      ],
    });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    fireEvent.click(await screen.findByTestId("purpose-relation-confirm-problem_to_change"));
    const rel = screen.getByTestId("purpose-relation-problem_to_change");
    expect(rel.className).toMatch(/flex-col/);
  });

  test("degraded_sections on the chain itself surface as a named note, not silence", async () => {
    const c = chain({ degraded_sections: ["relations"], degraded_detail: { relations: "boom" } });
    mockChainAndQuestion(c, null);
    await renderPanel({ sessionId: 7 });
    expect(await screen.findByTestId("purpose-frame-degraded")).toHaveTextContent("relations");
  });
});
