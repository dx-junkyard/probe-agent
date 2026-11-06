// Issue #455 (Epic #443 §6.1/§6.2): the hypothesis review + promote UI in
// `discussion-proposal-review.tsx`. Mocks `@/api/hooks` entirely so this
// exercises only the component's own rendering/promotion-request logic,
// never a real network call.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DiscussionProposalReview } from "@/components/discussion-proposal-review";
import type { AssistantDiscussionProposal, AssistantDiscussionThread } from "@/api/types";

const promoteMutate = vi.fn();
const promoteState = { isPending: false };
const listState = { isError: false, error: new Error("offline"), isFetching: false, refetch: vi.fn() };
const applyMutate = vi.fn();

function baseHypothesis(overrides: Partial<AssistantDiscussionProposal["hypotheses"][number]> = {}) {
  return {
    id: 1,
    proposal_id: 10,
    statement: "リトライは外部APIのレート制限に由来する",
    competing_explanations: ["単なる実装都合かもしれない"],
    refutation_conditions: ["設定ファイルにレート制限の記載が無ければ誤り"],
    next_investigation: "外部APIクライアントの設定を調査する",
    evidence_refs: [],
    uncertainty: "中",
    status: "proposed" as const,
    first_turn_number: 1,
    last_turn_number: 2,
    created_at: 0,
    schema_version: "discussion-hypothesis-v1",
    ...overrides,
  };
}

function baseProposal(hypotheses: AssistantDiscussionProposal["hypotheses"]): AssistantDiscussionProposal {
  return {
    id: 10, system_id: 1, thread_id: 1, screen_id: "ux-design-studio",
    target_kind: "ux_requirement", target_ref: "req-1",
    captured_target_revision_id: 1, captured_target_digest: "d",
    summary: "", confirmed_points: [], unresolved_questions: [], assumptions: [],
    evidence_refs: [], decision_method: "reasoning_llm", intelligence_run_id: null,
    provider: "openai", model: "gpt-5", prompt_version: "v1", schema_version: "v1",
    created_by: null, created_at: 0, items: [], hypotheses,
  };
}

const THREAD: AssistantDiscussionThread = {
  id: 1, system_id: 1, thread_key: "k", scope: "entity", screen_id: "ux-design-studio",
  target_kind: "ux_requirement", target_ref: "req-1", target_title: "Requirement 1",
  captured_target_digest: "d", status: "open", created_at: 0, updated_at: 0,
  schema_version: "v1",
} as AssistantDiscussionThread;

let currentProposal: AssistantDiscussionProposal;

vi.mock("@/api/hooks", () => ({
  useDiscussionProposals: () => ({ data: listState.isError ? undefined : { proposals: [currentProposal] }, isLoading: false, ...listState }),
  useCreateDiscussionProposal: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useDiscussionProposal: () => ({ data: currentProposal, isLoading: false, isError: false }),
  useApplyDiscussionProposalItems: () => ({ mutate: applyMutate, isPending: false, isError: false }),
  useRejectDiscussionProposalItems: () => ({ mutate: vi.fn(), isPending: false }),
  usePrefillDiscussionProposalItems: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePromoteDiscussionHypothesis: () => ({ mutate: promoteMutate, ...promoteState }),
}));

vi.mock("@/lib/ui-draft", () => ({ useUiDraftRegistry: () => null }));

function renderReview() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DiscussionProposalReview thread={THREAD} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DiscussionProposalReview hypothesis section (Issue #455)", () => {
  beforeEach(() => {
    promoteMutate.mockReset();
    promoteState.isPending = false;
    listState.isError = false;
    listState.refetch.mockReset();
    applyMutate.mockReset();
    currentProposal = baseProposal([baseHypothesis()]);
  });

  it("shows a list retrieval error with retry instead of an empty state or enabled generation", () => {
    listState.isError = true;
    renderReview();
    expect(screen.getByTestId("discussion-proposals-error")).toHaveTextContent("変更候補一覧を取得できませんでした");
    expect(screen.queryByTestId("discussion-proposal-empty")).not.toBeInTheDocument();
    expect(screen.getByTestId("discussion-proposal-generate")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));
    expect(listState.refetch).toHaveBeenCalledTimes(1);
  });

  it("keeps direct saving in a collapsed disclosure and explains that prefill stays unsaved", () => {
    currentProposal.items = [{ id: 5, item_kind: "field", field_name: "title", current_value: "以前", proposed_value: "変更後", rationale: "明確にする", eligibility: "appliable", status: "proposed", prefill_count: 0 }] as AssistantDiscussionProposal["items"];
    renderReview();
    expect(screen.getByText(/フォームへの反映は未保存の下書き/)).toBeInTheDocument();
    const direct = screen.getByTestId("discussion-proposal-apply");
    expect(direct.closest("details")).not.toHaveAttribute("open");
    expect(direct).not.toBeVisible();
    fireEvent.click(screen.getByText("詳細: フォームを使わず保存する"));
    expect(direct).toBeVisible();
    fireEvent.click(screen.getByTestId("discussion-proposal-item-select-5"));
    expect(applyMutate).not.toHaveBeenCalled();
    fireEvent.click(direct);
    expect(applyMutate).toHaveBeenCalledWith({ item_ids: [5] }, expect.any(Object));
  });

  it("renders the hypothesis with its competing_explanations/refutation_conditions/next_investigation", () => {
    renderReview();
    expect(screen.getByText("リトライは外部APIのレート制限に由来する")).toBeInTheDocument();
    expect(screen.getByText(/単なる実装都合かもしれない/)).toBeInTheDocument();
    expect(screen.getByText(/設定ファイルにレート制限の記載が無ければ誤り/)).toBeInTheDocument();
    expect(screen.getByText(/外部APIクライアントの設定を調査する/)).toBeInTheDocument();
  });

  it("promotes with a generated request_id and shows the resulting session id", async () => {
    promoteMutate.mockImplementation((_vars, { onSuccess }) => {
      onSuccess({
        hypothesis: baseHypothesis({ status: "promoted" }),
        promotion: {} as never,
        joint_understanding_session_id: 42,
        reused: false,
      });
    });
    renderReview();
    fireEvent.click(screen.getByTestId("discussion-hypothesis-promote-1"));
    expect(promoteMutate).toHaveBeenCalledTimes(1);
    const [vars] = promoteMutate.mock.calls[0];
    expect(vars.hypothesis_id).toBe(1);
    expect(typeof vars.request_id).toBe("string");
    expect(vars.request_id.length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(screen.getByTestId("discussion-hypothesis-promotion-status")).toHaveTextContent("#42"),
    );
  });

  it("reuses the SAME request_id on a retry after a failure (never mints a fresh one per click)", () => {
    // The button disables itself while a call is in flight (never a
    // double-submit) -- a genuine "second click" is a RETRY after the
    // first attempt has already resolved (here: failed).
    promoteMutate.mockImplementation((_vars, { onError }) => onError(new Error("network")));
    renderReview();
    fireEvent.click(screen.getByTestId("discussion-hypothesis-promote-1"));
    fireEvent.click(screen.getByTestId("discussion-hypothesis-promote-1"));
    expect(promoteMutate).toHaveBeenCalledTimes(2);
    const first = promoteMutate.mock.calls[0][0].request_id;
    const second = promoteMutate.mock.calls[1][0].request_id;
    expect(second).toBe(first);
  });

  it("shows a conflict message without crashing when the server refuses reuse for another hypothesis", async () => {
    promoteMutate.mockImplementation((_vars, { onError }) => {
      onError({
        response: {
          status: 409,
          data: { detail: { code: "discussion_hypothesis_promotion_request_conflict", message: "conflict" } },
        },
      });
    });
    renderReview();
    fireEvent.click(screen.getByTestId("discussion-hypothesis-promote-1"));
    await waitFor(() => expect(screen.getByTestId("discussion-hypothesis-promotion-status")).toBeInTheDocument());
  });

  it("renders hypotheses in their own section, never mixed into the field/relation items list", () => {
    currentProposal = baseProposal([baseHypothesis()]);
    renderReview();
    expect(screen.getByTestId("discussion-proposal-hypotheses")).toBeInTheDocument();
    // The proposal has zero field/relation/child items -- only the
    // hypothesis's own "empty items" message should show, proving the
    // hypothesis is never folded into that list.
    expect(screen.getByText("この提案には変更候補がありません。")).toBeInTheDocument();
  });
});
