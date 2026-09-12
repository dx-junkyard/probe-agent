// Issue #459 (Epic #457): `components/discussion-context-panel.tsx`.
//
// Mocks `@/api/hooks` entirely (the `discussion-proposal-review-hypothesis
// .test.tsx` pattern) so this exercises only the component's own
// rendering/wiring, never a real network call.
//
// Acceptance criteria this file proves on the Dashboard side:
// 1. The server-decided `next_action` reason renders verbatim, and this
//    panel offers its own "照合" button ONLY for the two kinds it can
//    advance (`match`/`evidence_stale`) -- DD-UX-01: never re-deriving the
//    priority table, never a second competing primary action.
// 2. `degraded_sections` renders its own notice without hiding the action.
// 3. The context bundle renders as progressive disclosure (`<details>`),
//    one entry per section, labelled by its `operation_state`.
// 4. A successful 照合 call renders returned claims with their kind and,
//    for a citation resolvable to a `selected` deep link, a real link;
//    otherwise plain text -- never a client-guessed URL.
// 5. A failed 照合 call (`error_kind`) renders its mapped Japanese message.

import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DiscussionContextPanel } from "@/components/discussion-context-panel";
import type {
  AssistantDiscussionThreadDetailOut, DiscussionContextBundle, DiscussionContextClaimsResultOut,
} from "@/api/types";

const claimsMutate = vi.fn();
const claimsState: { isPending: boolean; data: DiscussionContextClaimsResultOut | undefined } = {
  isPending: false, data: undefined,
};
const expandMutate = vi.fn();

let bundle: DiscussionContextBundle | undefined;

vi.mock("@/api/hooks", () => ({
  useAssistantDiscussionThreadDetail: () => ({ data: undefined }),
  useDiscussionContextBundle: () => ({ data: bundle, isLoading: false, isError: false, refetch: vi.fn() }),
  useExpandDiscussionContext: () => ({ mutate: expandMutate, isPending: false }),
  useCreateDiscussionContextClaims: () => ({
    mutate: claimsMutate, isPending: claimsState.isPending, data: claimsState.data,
  }),
}));

function baseThread(
  nextAction: AssistantDiscussionThreadDetailOut["next_action"],
): AssistantDiscussionThreadDetailOut {
  return {
    thread: {
      id: 1, system_id: 1, thread_key: "k", scope: "entity", screen_id: "objective-map",
      target_kind: "product_gap", target_ref: "gap-1", target_title: "Gap 1",
      captured_target_revision_id: null, captured_target_digest: "d", status: "open",
      created_by: null, created_at: 0, updated_at: 0,
      schema_version: "v1",
    },
    target_state: "current",
    capabilities: [],
    turns: [],
    next_action: nextAction,
  };
}

function baseBundle(overrides: Partial<DiscussionContextBundle> = {}): DiscussionContextBundle {
  return {
    schema_version: "v1", bundle_digest: "bd1",
    root: { target_kind: "product_gap", target_ref: "gap-1", revision_id: 1, digest: "rd" },
    snapshot: { id: 1, commit_sha: "abcdef1234567890" },
    sections: [
      {
        section_id: "related", operation_state: "available",
        facts: [
          {
            target_kind: "ux_journey", target_ref: "j1", title: "Journey 1", revision_id: 1,
            digest: "jd", resolution: "resolved", facts: {}, truncated: false,
          },
        ],
        coverage: { returned_count: 1, total_count: 1, completeness: "complete", stop_reason: "complete", continuation: null },
      },
      {
        section_id: "stakeholder", operation_state: "unsupported",
        facts: [],
        coverage: { returned_count: 0, total_count: null, completeness: "unknown", stop_reason: "unsupported", continuation: null },
      },
    ],
    sources: [
      {
        source_id: "product_gap:gap-1", target_kind: "product_gap", target_ref: "gap-1",
        revision_id: 1, digest: "rd", snapshot_id: 1, freshness: "current",
        deep_link: "/objective-map?view=gaps&gap=gap-1", deep_link_state: "selected",
      },
      {
        source_id: "ux_journey:j1", target_kind: "ux_journey", target_ref: "j1",
        revision_id: 1, digest: "jd", snapshot_id: 1, freshness: "current",
        deep_link: null, deep_link_state: "unavailable",
      },
    ],
    dependencies: [],
    next_action: { kind: "none", target: "", enabled: false, reason: "" },
    ...overrides,
  };
}

function renderPanel(thread: AssistantDiscussionThreadDetailOut) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DiscussionContextPanel thread={thread} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DiscussionContextPanel (Issue #459)", () => {
  beforeEach(() => {
    claimsMutate.mockReset();
    expandMutate.mockReset();
    claimsState.isPending = false;
    claimsState.data = undefined;
    bundle = baseBundle();
  });

  it("renders the server's next_action reason and offers 照合 for kind=match", () => {
    renderPanel(baseThread({ kind: "match", reason: "目的・UX・機能を照合してください。", target_ref: null, degraded_sections: [] }));
    expect(screen.getByTestId("discussion-next-action-label")).toHaveTextContent("目的・UX・機能の照合");
    expect(screen.getByText("目的・UX・機能を照合してください。")).toBeInTheDocument();
    expect(screen.getByTestId("discussion-context-match")).toBeInTheDocument();
  });

  it("does not render a match button for a kind this panel does not own (review_proposal)", () => {
    renderPanel(baseThread({ kind: "review_proposal", reason: "変更候補があります。", target_ref: "3", degraded_sections: [] }));
    expect(screen.getByText("変更候補があります。")).toBeInTheDocument();
    expect(screen.queryByTestId("discussion-context-match")).not.toBeInTheDocument();
  });

  it("renders degraded_sections as a separate notice without removing the primary action", () => {
    renderPanel(baseThread({ kind: "match", reason: "目的・UX・機能を照合してください。", target_ref: null, degraded_sections: ["investigation"] }));
    expect(screen.getByTestId("discussion-next-action-degraded")).toHaveTextContent("investigation");
    expect(screen.getByTestId("discussion-context-match")).toBeInTheDocument();
  });

  it("invokes the claims mutation with the current question text", () => {
    renderPanel(baseThread({ kind: "match", reason: "", target_ref: null, degraded_sections: [] }));
    fireEvent.change(screen.getByTestId("discussion-context-question"), { target: { value: "十分か" } });
    fireEvent.click(screen.getByTestId("discussion-context-match"));
    expect(claimsMutate).toHaveBeenCalledTimes(1);
    expect(claimsMutate.mock.calls[0][0]).toEqual({ question: "十分か" });
  });

  it("renders each context bundle section labelled by its operation_state", () => {
    renderPanel(baseThread({ kind: "match", reason: "", target_ref: null, degraded_sections: [] }));
    const sections = screen.getAllByTestId("discussion-context-section");
    expect(sections).toHaveLength(2);
    expect(sections[0]).toHaveTextContent("related");
    expect(sections[0]).toHaveTextContent("取得済み");
    expect(sections[1]).toHaveTextContent("stakeholder");
    expect(sections[1]).toHaveTextContent("未登録");
  });

  it("renders a resolvable citation as a real link and an unresolvable one as plain text", () => {
    claimsState.data = {
      provider: "openai", model: "gpt-5", is_mock: false, prompt_version: "v1", schema_version: "v1",
      decision_method: "reasoning_llm",
      claims: [
        { kind: "fact", statement: "設定手順あり", cited_source_ids: ["product_gap:gap-1"], basis: "reasoning_llm" },
        { kind: "unknown", statement: "案内は不明", cited_source_ids: ["ux_journey:j1"], basis: "reasoning_llm" },
      ],
      scope_note: "related: 1/1件(complete)", as_of_snapshot_commit: "abcdef1234567890",
      retried: false, error: null, error_kind: null, thread_id: 1, turn_number: 2,
    };
    renderPanel(baseThread({ kind: "match", reason: "", target_ref: null, degraded_sections: [] }));

    const claims = screen.getAllByTestId("discussion-context-claim");
    expect(claims).toHaveLength(2);
    // Issue #459 (docs/01-specifications/ux/decision-discussion-workflow.md §2): the citation carries a `returnTo`
    // reference back to THIS thread's own deep link -- never a client-
    // guessed URL, and never a copy of any draft content.
    expect(screen.getByTestId("discussion-context-citation")).toHaveAttribute(
      "href", "/objective-map?view=gaps&gap=gap-1&returnTo=%2Fobjective-map%3Fview%3Dgaps%26gap%3Dgap-1",
    );
    expect(screen.getByTestId("discussion-context-citation-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("discussion-context-scope-note")).toHaveTextContent("related: 1/1件(complete)");
  });

  it("renders the mapped Japanese message for an invalid_citation failure", () => {
    claimsState.data = {
      provider: "openai", model: "gpt-5", is_mock: false, prompt_version: "v1", schema_version: "v1",
      decision_method: "reasoning_llm", claims: [], scope_note: "", as_of_snapshot_commit: null,
      retried: true, error: "bad citation", error_kind: "invalid_citation", thread_id: 1, turn_number: null,
    };
    renderPanel(baseThread({ kind: "match", reason: "", target_ref: null, degraded_sections: [] }));
    expect(screen.getByTestId("discussion-context-claims-error")).toHaveTextContent("根拠として存在しない引用");
  });
  it("keeps the first page and exposes the next page and its source link", () => {
    bundle = baseBundle();
    bundle.sections[0].coverage = { returned_count: 1, total_count: 2, completeness: "partial", stop_reason: "item_budget", continuation: "next" };
    const page = baseBundle();
    page.sections[0].facts = [{ ...page.sections[0].facts[0], target_ref: "j2", title: "Journey 2" }];
    page.sections[0].coverage = { returned_count: 1, total_count: 2, completeness: "complete", stop_reason: "complete", continuation: null };
    page.sources = [{ ...page.sources[1], source_id: "ux_journey:j2", target_ref: "j2", deep_link: "/ux-design-studio?tab=journeys&journey=j2", deep_link_state: "selected" }];
    expandMutate.mockImplementation((_payload, callbacks) => { callbacks.onSuccess({bundle: page, expanded_section_ids: ["related"]}); callbacks.onSettled(); });
    renderPanel(baseThread({ kind: "match", reason: "", target_ref: null, degraded_sections: [] }));
    fireEvent.click(screen.getByTestId("discussion-context-section-expand"));
    expect(screen.getByText("Journey 1")).toBeInTheDocument();
    expect(screen.getByText("Journey 2").closest("a")).toHaveAttribute("href", expect.stringContaining("returnTo="));
    expect(screen.queryByTestId("discussion-context-section-expand")).toBeNull();
  });

});
