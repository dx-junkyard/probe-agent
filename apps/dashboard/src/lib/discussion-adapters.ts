// The single DiscussionAdapter registry (Issue #444, Epic #443 Phase 1).
//
// docs/01-specifications/capabilities/ai-discussion-adapter.md §1.5/§1.6 is the canonical contract. Before
// this module existed, `components/assistant-panel.tsx`'s
// `deriveDiscussionCandidate` had one `if (screenId === "...")` branch per
// discussion-enabled screen, each hand-listing which route params make which
// target_kind selectable there. This module is the single place that lives
// now: one `DashboardDiscussionAdapter` per `DiscussionTargetKind`, each
// owning its own `resolveFromRoute`, so the panel itself never branches on a
// screen id for discussion purposes again.
//
// Phase 1 is a MOVE, not a redesign: every `resolveFromRoute` below reads
// the exact same route params, in the exact same priority, that the old
// per-screen `if` chain did -- `src/__tests__/assistant-discussion-thread.
// test.tsx` and `assistant-voice.test.tsx` are what prove the move changed
// nothing observable.
//
// `forms` stayed `[]` for every adapter through Phase 1 (#446 populates the
// prefill machinery that CONSUMES `forms` later); Phase 2 (#445) is the first
// to populate it, for the four kinds whose Dashboard forms exist today
// (`ux_journey` / `ux_journey_step` / `ux_requirement` / `solution_design`).
// `invalidateKeys` and `deepLink` were implemented in Phase 1 already -- they
// are read-only conveniences (which caches to refresh, which URL opens the
// target) that never depended on the unsaved-draft or prefill machinery.

import type {
  AssistantDiscussionProposal,
  AssistantDiscussionTargetIn,
  DiscussionOperationResult,
  DiscussionScope,
  DiscussionTargetKind,
} from "@/api/types";
import { sysKey } from "@/api/hooks";
import type { FormDraftPatch } from "@/lib/form-draft-inbox";

export interface DiscussionCandidate {
  target: AssistantDiscussionTargetIn;
  label: string;
}

/** §2.2/§2.3 (Issue #445/#446). Declared now so a later phase adds a
 * populated `forms` array instead of a new field on this interface. */
export interface UiDraftFormBinding {
  formId: string;
  fields: readonly string[];
}

export interface DashboardDiscussionAdapter {
  targetKind: DiscussionTargetKind;
  scope: DiscussionScope;
  screenIds: readonly string[];
  /** Japanese display name (singular noun) -- mirrors the server adapter's
   * own `label` (docs/01-specifications/capabilities/ai-discussion-adapter.md §1.4). Established
   * product-concept terms (Journey, Requirement, Solution Design) stay
   * English per CLAUDE.md's Dashboard UI言語規約 initial-mention rule. */
  label: string;
  /** Resolve the most specific selectable target from the URL + screen id.
   * `null` = nothing more specific than "the whole screen" is selected. */
  resolveFromRoute(screenId: string, params: URLSearchParams): DiscussionCandidate | null;
  /** Issue #456: the versioned id of the client-side IMPLEMENTATION handler
   * that can actually carry out a prefill dispatch (navigate -> mount ->
   * deliver -> ack, #452's job) for this kind. `null` means "no handler is
   * wired yet" -- declaring `forms` below only says a destination form
   * EXISTS, not that anything can deliver to it. `tests/
   * test_discussion_contract_parity.py` checks this against the server
   * adapter's own `prefill_handler_id`: the derived `prefill_form`
   * capability is `true` only when BOTH sides declare a MATCHING id, never
   * from this field alone (a client cannot self-report the capability on).
   */
  prefillHandlerId: string | null;
  /** Prefill destinations (Issue #446). Empty through Phase 1-4. */
  forms: readonly UiDraftFormBinding[];
  /** React Query key PREFIXES to invalidate after a canonical write against
   * this target (`invalidateQueries` matches by prefix, so a prefix missing
   * the target_ref still invalidates every variant of that query). */
  invalidateKeys(targetRef: string): readonly (readonly unknown[])[];
  /** The URL that opens this target on its (primary) screen. Navigates --
   * never executes (docs/01-specifications/capabilities/ai-discussion-adapter.md §3.6 / #358 / #427's CTA
   * rule carried over from the parent Epic). `null` when no screen can
   * plausibly render this ref. */
  deepLink(targetRef: string): string | null;
}

const SCREEN_PATH: Record<string, string> = {
  overview: "/",
  interview: "/interview",
  "ux-design-studio": "/ux-design-studio",
  "journey-blueprint": "/journey-blueprint",
  // Issue #453 (Epic #443 Phase 4, §4.1's Decisions). `capability-map` gains
  // only the whole-screen conversation this table already gives every
  // member -- none of the 8 new target_kinds render there (see
  // `discussion_adapters.py`'s own comment on `DISCUSSION_SCREEN_IDS`).
  "objective-map": "/objective-map",
  "stakeholder-value-network": "/stakeholder-value-network",
  "capability-map": "/capability-map",
};

const UX_DESIGN_STUDIO_AND_BLUEPRINT = ["ux-design-studio", "journey-blueprint"] as const;

function journeyKeyOf(targetRef: string): string {
  return targetRef.split("#", 1)[0] ?? "";
}

const screenAdapter: DashboardDiscussionAdapter = {
  targetKind: "screen",
  scope: "screen",
  screenIds: Object.keys(SCREEN_PATH),
  label: "画面",
  // The "whole screen" target is always available and is constructed
  // directly by the panel (it is the fallback, not a "more specific"
  // candidate) -- this adapter exists for registry completeness (every
  // DiscussionTargetKind has exactly one adapter) rather than for candidate
  // resolution, so it never wins a `resolveDiscussionCandidate` scan.
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [],
  deepLink: (targetRef) => SCREEN_PATH[targetRef] ?? null,
};

const interviewSessionAdapter: DashboardDiscussionAdapter = {
  targetKind: "interview_session",
  scope: "entity",
  screenIds: ["interview"],
  label: "セッション",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "interview") return null;
    const session = params.get("session");
    if (!session) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "interview_session", target_ref: session },
      label: `セッション #${session}`,
    };
  },
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: (targetRef) => {
    const sessionId = Number(targetRef);
    if (!Number.isFinite(sessionId)) return [sysKey("interviewSession")];
    return [[...sysKey("interviewSession"), sessionId], sysKey("understandingBrief")];
  },
  deepLink: (targetRef) => `/interview?session=${encodeURIComponent(targetRef)}`,
};

// `understanding_claim` / `overview_finding` (below) have never had a route
// derivation on the Dashboard -- no screen currently offers a "discuss this
// claim/finding" action, only `tests/test_assistant_discussion_proposals.py`
// creates threads for them directly. `resolveFromRoute` returning `null`
// unconditionally preserves that (Issue #444 does not add new UI reach).

const understandingClaimAdapter: DashboardDiscussionAdapter = {
  targetKind: "understanding_claim",
  // Both screens that render the Understanding Brief, matching the server
  // adapter. `screenIds` says where the target legitimately LIVES, which is
  // a different question from which screens auto-derive it today.
  scope: "element",
  screenIds: ["overview", "interview"],
  label: "理解の主張",
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [sysKey("understandingBrief"), sysKey("overview")],
  deepLink: () => SCREEN_PATH.overview,
};

const overviewFindingAdapter: DashboardDiscussionAdapter = {
  targetKind: "overview_finding",
  scope: "element",
  screenIds: ["overview"],
  label: "発見事項",
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [sysKey("overview")],
  deepLink: () => SCREEN_PATH.overview,
};

const uxJourneyAdapter: DashboardDiscussionAdapter = {
  targetKind: "ux_journey",
  scope: "entity",
  screenIds: UX_DESIGN_STUDIO_AND_BLUEPRINT,
  label: "Journey",
  resolveFromRoute: (screenId, params) => {
    const journey = params.get("journey");
    if (!journey) return null;
    if (screenId === "ux-design-studio") {
      const tab = params.get("tab") || "journeys";
      if (tab !== "journeys") return null;
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "ux_journey", target_ref: journey },
        label: `Journey「${journey}」`,
      };
    }
    if (screenId === "journey-blueprint") {
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "ux_journey", target_ref: journey },
        label: `Journey「${journey}」`,
      };
    }
    return null;
  },
  // Issue #445: `components/ux-design/journey-panel.tsx`'s
  // `JourneyRevisionForm` -- the SAME field names `_UX_JOURNEY_FIELDS`
  // registers server-side (`app/discussion_adapters.py`), which are in turn
  // `ux_design.add_journey_revision`'s own keyword params.
  prefillHandlerId: null,
  forms: [
    {
      formId: "ux_journey.revision",
      fields: ["title", "beneficiary", "usage_context", "entry_trigger", "value_arrival", "summary"],
    },
  ],
  invalidateKeys: (targetRef) => [sysKey("ux-journeys"), [...sysKey("ux-journey"), targetRef]],
  deepLink: (targetRef) => `/ux-design-studio?tab=journeys&journey=${encodeURIComponent(targetRef)}`,
};

const uxJourneyStepAdapter: DashboardDiscussionAdapter = {
  targetKind: "ux_journey_step",
  scope: "element",
  screenIds: UX_DESIGN_STUDIO_AND_BLUEPRINT,
  label: "ステップ",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "ux-design-studio" && screenId !== "journey-blueprint") return null;
    const journey = params.get("journey");
    const step = params.get("step");
    if (!journey || !step) return null;
    return {
      target: {
        scope: "element", screen_id: screenId, target_kind: "ux_journey_step",
        target_ref: `${journey}#${step}`,
      },
      label: `ステップ「${step}」`,
    };
  },
  // Issue #445: the step rows inside the SAME `JourneyRevisionForm`.
  prefillHandlerId: null,
  forms: [
    {
      formId: "ux_journey_step.revision",
      fields: [
        "user_intent", "system_response", "success_criteria",
        "failure_mode", "recovery_path", "evidence_expectation",
      ],
    },
  ],
  invalidateKeys: (targetRef) => [
    sysKey("ux-journeys"),
    [...sysKey("ux-journey"), journeyKeyOf(targetRef)],
  ],
  deepLink: (targetRef) => {
    const parts = targetRef.split("#");
    if (parts.length !== 2 || parts.some((part) => !part)) return null;
    const [journey, step] = parts;
    return `/ux-design-studio?tab=journeys&journey=${encodeURIComponent(journey)}&step=${encodeURIComponent(step)}`;
  },
};

const uxRequirementAdapter: DashboardDiscussionAdapter = {
  targetKind: "ux_requirement",
  scope: "entity",
  screenIds: ["ux-design-studio"],
  label: "Requirement",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "ux-design-studio") return null;
    const tab = params.get("tab") || "journeys";
    const requirement = params.get("requirement");
    if (tab !== "requirements" || !requirement) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "ux_requirement", target_ref: requirement },
      label: `Requirement「${requirement}」`,
    };
  },
  // Requirement revision fields and keyed acceptance criteria have separate receivers.
  // Issue #452: the first real prefill delivery handler, matching the
  // server adapter's own `prefill_handler_id` (parity-checked by
  // `tests/test_discussion_contract_parity.py`). The handler itself is
  // `dispatchFormDraftPrefill` in `lib/form-draft-inbox.ts`, invoked by
  // `components/discussion-proposal-review.tsx`.
  prefillHandlerId: "ux_requirement.revision@v1",
  forms: [
    {
      formId: "ux_requirement.revision",
      fields: ["statement", "rationale", "constraint_text", "out_of_scope_note"],
    },
  ],
  invalidateKeys: (targetRef) => [sysKey("ux-requirements"), [...sysKey("ux-requirement"), targetRef]],
  deepLink: (targetRef) => `/ux-design-studio?tab=requirements&requirement=${encodeURIComponent(targetRef)}`,
};

const solutionDesignAdapter: DashboardDiscussionAdapter = {
  targetKind: "solution_design",
  scope: "entity",
  screenIds: ["ux-design-studio"],
  label: "Solution Design",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "ux-design-studio") return null;
    const tab = params.get("tab") || "journeys";
    const design = params.get("design");
    if (tab !== "solutions" || !design) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "solution_design", target_ref: design },
      label: `Solution Design「${design}」`,
    };
  },
  // Issue #445: `components/ux-design/solution-design-panel.tsx`'s
  // `AddOptionForm` -- it drafts a NEW option, so the in-progress
  // `option_key` is the draft's `selected_item_ref`, not a registered field.
  prefillHandlerId: null,
  forms: [
    { formId: "solution_design.option", fields: ["title", "approach", "tradeoffs", "risks"] },
  ],
  invalidateKeys: (targetRef) => [sysKey("solution-designs"), [...sysKey("solution-design"), targetRef]],
  deepLink: (targetRef) => `/ux-design-studio?tab=solutions&design=${encodeURIComponent(targetRef)}`,
};

const blueprintLaneCellAdapter: DashboardDiscussionAdapter = {
  targetKind: "blueprint_lane_cell",
  scope: "element",
  screenIds: ["journey-blueprint"],
  label: "レーンセル",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "journey-blueprint") return null;
    const journey = params.get("journey");
    const step = params.get("step");
    const lane = params.get("lane");
    if (!journey || !step || !lane) return null;
    return {
      target: {
        scope: "element", screen_id: screenId, target_kind: "blueprint_lane_cell",
        target_ref: `${journey}#${step}#${lane}`,
      },
      label: `${lane}(「${step}」)`,
    };
  },
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: (targetRef) => {
    const journeyKey = journeyKeyOf(targetRef);
    return [
      [...sysKey("journey-blueprint"), journeyKey],
      [...sysKey("journey-blueprint-diff"), journeyKey],
    ];
  },
  deepLink: (targetRef) => {
    const parts = targetRef.split("#");
    if (parts.length !== 3 || parts.some((part) => !part)) return null;
    const [journey, step, lane] = parts;
    return `/journey-blueprint?journey=${encodeURIComponent(journey)}&step=${encodeURIComponent(step)}&lane=${encodeURIComponent(lane)}`;
  },
};

// --- Issue #453 (Epic #443 Phase 4, #447's follow-up): Vision-to-Feature ---
// docs/01-specifications/capabilities/ai-discussion-adapter.md §4.1's table. None of the 8 declare `forms`
// (generation/confirmation for Objective/UX/Feature content is out of this
// Issue's scope, its own Scope/Ownership section). `purpose_element` /
// `purpose_relation` / `stakeholder_need` / `product_feature` have no live
// selection on the Dashboard today, so `resolveFromRoute` stays `null` --
// the exact `understanding_claim`/`overview_finding` precedent above.

const purposeElementAdapter: DashboardDiscussionAdapter = {
  targetKind: "purpose_element",
  scope: "element",
  screenIds: ["overview", "interview"],
  label: "Purpose 要素",
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [sysKey("purpose-chain"), sysKey("overview")],
  deepLink: () => SCREEN_PATH.overview,
};

const purposeRelationAdapter: DashboardDiscussionAdapter = {
  targetKind: "purpose_relation",
  scope: "element",
  screenIds: ["overview", "interview"],
  label: "Purpose 関係",
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [sysKey("purpose-chain"), sysKey("overview")],
  deepLink: () => SCREEN_PATH.overview,
};

const stakeholderAdapter: DashboardDiscussionAdapter = {
  targetKind: "stakeholder",
  scope: "entity",
  screenIds: ["stakeholder-value-network"],
  label: "Stakeholder",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "stakeholder-value-network") return null;
    const node = params.get("node");
    if (!node) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "stakeholder", target_ref: node },
      label: `Stakeholder「${node}」`,
    };
  },
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [sysKey("stakeholder-value-network")],
  deepLink: (targetRef) => `/stakeholder-value-network?node=${encodeURIComponent(targetRef)}`,
};

const stakeholderNeedAdapter: DashboardDiscussionAdapter = {
  targetKind: "stakeholder_need",
  scope: "entity",
  screenIds: ["stakeholder-value-network"],
  label: "Need",
  // §7.1's projection folds Needs into a Stakeholder's `evidence_state`
  // rather than listing them as their own graph node -- there is no `need`
  // selection param to read yet.
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: () => [sysKey("stakeholder-value-network")],
  deepLink: () => SCREEN_PATH["stakeholder-value-network"],
};

const productObjectiveAdapter: DashboardDiscussionAdapter = {
  targetKind: "product_objective",
  scope: "entity",
  screenIds: ["objective-map"],
  label: "Objective",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "objective-map") return null;
    if ((params.get("view") || "objectives") === "gaps") return null;
    const objective = params.get("objective");
    if (!objective) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "product_objective", target_ref: objective },
      label: `Objective「${objective}」`,
    };
  },
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: (targetRef) => [
    sysKey("objective-map"), [...sysKey("product-objective"), targetRef],
  ],
  deepLink: (targetRef) => `/objective-map?objective=${encodeURIComponent(targetRef)}`,
};

const productMilestoneAdapter: DashboardDiscussionAdapter = {
  targetKind: "product_milestone",
  scope: "entity",
  screenIds: ["objective-map"],
  label: "Milestone",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "objective-map") return null;
    if ((params.get("view") || "objectives") === "gaps") return null;
    const milestone = params.get("milestone");
    if (!milestone) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "product_milestone", target_ref: milestone },
      label: `Milestone「${milestone}」`,
    };
  },
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: (targetRef) => [
    sysKey("objective-map"), sysKey("gap-workbench"), [...sysKey("product-milestone"), targetRef],
  ],
  deepLink: (targetRef) => `/objective-map?milestone=${encodeURIComponent(targetRef)}`,
};

const productGapAdapter: DashboardDiscussionAdapter = {
  targetKind: "product_gap",
  scope: "entity",
  // Gap Workbench is a VIEW inside `objective-map` (`?view=gaps`), never a
  // screen of its own (§4.1's Decisions).
  screenIds: ["objective-map"],
  label: "Gap",
  resolveFromRoute: (screenId, params) => {
    if (screenId !== "objective-map") return null;
    if ((params.get("view") || "objectives") !== "gaps") return null;
    const gap = params.get("gap");
    if (!gap) return null;
    return {
      target: { scope: "entity", screen_id: screenId, target_kind: "product_gap", target_ref: gap },
      label: `Gap「${gap}」`,
    };
  },
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: (targetRef) => [
    sysKey("gap-workbench"), sysKey("objective-map"), [...sysKey("product-gap"), targetRef],
  ],
  deepLink: (targetRef) => `/objective-map?view=gaps&gap=${encodeURIComponent(targetRef)}`,
};

const productFeatureAdapter: DashboardDiscussionAdapter = {
  targetKind: "product_feature",
  scope: "entity",
  // Referenced (read-only link lists) from both screens -- neither has a
  // `feature` selection route param of its own yet (see
  // `requirement-panel.tsx` / `gap-workbench-panel.tsx`).
  screenIds: ["ux-design-studio", "objective-map"],
  label: "Feature",
  resolveFromRoute: () => null,
  prefillHandlerId: null,
  forms: [],
  invalidateKeys: (targetRef) => [
    sysKey("product-features"), [...sysKey("product-feature"), targetRef],
  ],
  // No screen of its own yet -- the same honest `null`
  // `components/functional-lineage/model.ts`'s `lineageDeepLink` already
  // returns for this exact kind, rather than substituting a plausible URL.
  deepLink: () => null,
};

/** §1.4/§1.5's registry: exactly one adapter per `DiscussionTargetKind`.
 * `tests/test_discussion_contract_parity.py` checks this set against the
 * server registry's `target_kind` set and against `DiscussionTargetKind`
 * itself. */
export const DISCUSSION_ADAPTERS: Record<DiscussionTargetKind, DashboardDiscussionAdapter> = {
  screen: screenAdapter,
  interview_session: interviewSessionAdapter,
  understanding_claim: understandingClaimAdapter,
  overview_finding: overviewFindingAdapter,
  ux_journey: uxJourneyAdapter,
  ux_journey_step: uxJourneyStepAdapter,
  ux_requirement: uxRequirementAdapter,
  solution_design: solutionDesignAdapter,
  blueprint_lane_cell: blueprintLaneCellAdapter,
  purpose_element: purposeElementAdapter,
  purpose_relation: purposeRelationAdapter,
  stakeholder: stakeholderAdapter,
  stakeholder_need: stakeholderNeedAdapter,
  product_objective: productObjectiveAdapter,
  product_milestone: productMilestoneAdapter,
  product_gap: productGapAdapter,
  product_feature: productFeatureAdapter,
};

/**
 * Per-screen candidate priority (docs/01-specifications/capabilities/ai-discussion-adapter.md §1.5: "a
 * fixed priority within one adapter set -- keep the existing precedence
 * exactly"). This is the ONLY place screen identity governs which adapter
 * wins -- `assistant-panel.tsx` itself never branches on `screenId` for
 * discussion purposes. Order is most-specific-first, matching the old
 * per-screen `if` chain in `deriveDiscussionCandidate` exactly:
 *   - ux-design-studio: step > requirement > design > journey
 *   - journey-blueprint: lane cell > step > journey
 *
 * Issue #453 adds `objective-map` (gap > milestone > objective -- a Gap
 * selection is only reachable in the `?view=gaps` lane, which is why each
 * of the three's own `resolveFromRoute` already excludes the other two's
 * view) and `stakeholder-value-network` (the only entity selectable there
 * today is `?node=`). `capability-map` gets no entry -- no entity/element
 * kind from this Issue renders there (whole-screen conversation still
 * works via the `screen` adapter, which never consults this table).
 */
const CANDIDATE_PRIORITY: Record<string, readonly DiscussionTargetKind[]> = {
  interview: ["interview_session"],
  "ux-design-studio": ["ux_journey_step", "ux_requirement", "solution_design", "ux_journey"],
  "journey-blueprint": ["blueprint_lane_cell", "ux_journey_step", "ux_journey"],
  overview: [],
  "objective-map": ["product_gap", "product_milestone", "product_objective"],
  "stakeholder-value-network": ["stakeholder"],
};

/** Replaces `deriveDiscussionCandidate`: the most specific selectable
 * non-screen target for this screen + URL, or `null` when nothing beats
 * "the whole screen". */
export function resolveDiscussionCandidate(screenId: string, search: string): DiscussionCandidate | null {
  const params = new URLSearchParams(search);
  const order = CANDIDATE_PRIORITY[screenId];
  if (!order) return null;
  for (const kind of order) {
    const candidate = DISCUSSION_ADAPTERS[kind].resolveFromRoute(screenId, params);
    if (candidate) return candidate;
  }
  return null;
}

// --- Proposal -> form draft patch (Issue #446, Epic #443 Phase 3) ------------
// docs/ai-discussion-adapter.md §3.2. Maps SELECTED proposal items onto the
// adapter's own registered form field allowlist -- never the field names a
// server registry drift might have introduced. An item whose `field_name`
// falls outside the form's allowlist is reported (`unregisteredFieldNames`),
// never silently dropped: the developer selected it, so the review UI must
// say it could not be carried rather than deliver a partial patch that looks
// complete.

function generatePatchToken(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `patch-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export interface ProposalToDraftResult {
  /** `null` when the adapter has no registered form at all -- there is
   * nothing to prefill into, distinct from "some fields were rejected". */
  patch: FormDraftPatch | null;
  formId: string | null;
  unregisteredFieldNames: string[];
}

/** Builds a `FormDraftPatch` from the proposal's items whose `id` is in
 * `itemIds`. Today every adapter registers at most one form per target_kind
 * (the same assumption `assistant-panel.tsx`'s `captureUiDraft` already
 * makes), so the first `forms` entry is not a guess -- it is the only entry
 * there is. */
export function proposalToDraft(
  adapter: DashboardDiscussionAdapter,
  proposal: AssistantDiscussionProposal,
  itemIds: readonly number[],
): ProposalToDraftResult {
  if (adapter.forms.length === 0) {
    return { patch: null, formId: null, unregisteredFieldNames: [] };
  }
  const binding = adapter.forms[0];
  const idSet = new Set(itemIds);
  const selected = proposal.items.filter((item) => idSet.has(item.id));

  const fields: FormDraftPatch["fields"] = [];
  const relations: FormDraftPatch["relations"] = [];
  // Issue #454: keyed by `childKind|childKey|intent` so every SELECTED
  // item that addresses the same child (e.g. one Acceptance Criterion's
  // `statement` and `verification_method`, proposed as two separate item
  // rows -- §5.1) lands on ONE `childOps` entry rather than one per field.
  const childOpsByAddress = new Map<string, FormDraftPatch["childOps"][number]>();
  const unregisteredFieldNames: string[] = [];
  let selectedItemRef = "";

  for (const item of selected) {
    if (item.item_kind !== "field") {
      relations.push({
        relationKind: item.relation_kind,
        targetKind: item.relation_target_kind,
        targetRef: item.relation_target_ref,
        note: item.rationale,
      });
      continue;
    }
    // A child item is STILL `item_kind === "field"`, distinguished by a
    // non-empty `child_kind` -- it must never be matched against the
    // top-level `binding.fields` allowlist, since a child field name
    // (e.g. an Acceptance Criterion's own "statement") can collide by
    // NAME with an unrelated top-level field of the same target_kind
    // (the Requirement's own "statement"). Routing it into `childOps`
    // here, before the top-level check, is what keeps the two apart.
    if (item.child_kind) {
      const address = `${item.child_kind}|${item.child_key}|${item.child_intent}`;
      let op = childOpsByAddress.get(address);
      if (!op) {
        op = {
          childKind: item.child_kind,
          childKey: item.child_key,
          intent: (item.child_intent || "update") as "add" | "update" | "remove",
          order: item.child_order,
          fields: [],
        };
        childOpsByAddress.set(address, op);
      }
      // A later-selected item's own `child_order` (a pure reorder row,
      // §5.1) still applies even though this address's FIRST item did not
      // carry one -- `??` only fills a still-unset order, never clobbers
      // one already captured from an earlier item at this same address.
      if (op.order === null && item.child_order !== null) op.order = item.child_order;
      if (item.field_name) {
        op.fields.push({ fieldName: item.field_name, value: item.proposed_value });
      }
      continue;
    }
    if (!binding.fields.includes(item.field_name)) {
      unregisteredFieldNames.push(item.field_name);
      continue;
    }
    fields.push({ fieldName: item.field_name, value: item.proposed_value, rationale: item.rationale });
    if (item.subject_ref) selectedItemRef = item.subject_ref;
  }

  const patch: FormDraftPatch = {
    patchToken: generatePatchToken(),
    targetKind: proposal.target_kind,
    targetRef: proposal.target_ref,
    formId: binding.formId,
    selectedItemRef,
    fields,
    childOps: Array.from(childOpsByAddress.values()),
    relations,
  };
  return { patch, formId: binding.formId, unregisteredFieldNames };
}

// --- Discussion error presentation (Issue #456) ------------------------------
// docs/01-specifications/capabilities/ai-discussion-adapter.md §1.3/§9's UI display contract: "未対応時は
// 理由と既存画面への移動を示し、失敗時には再試行を示す" ("show the reason and a
// route to the existing screen when unsupported; show a retry when it merely
// failed"). This is the single place that translates the server's finite
// discussion-adapter 422 codes (§1.7) into that distinction -- `assistant-
// panel.tsx`'s error bubble is the one caller today, but any future discussion
// surface should call this rather than growing its own copy of the code list.

/** The finite discussion-adapter 422 codes this module knows how to explain,
 * each mapped to its `DiscussionOperationResult` and a Japanese message. A
 * code NOT in this table is treated as a plain `unavailable` failure (a
 * network error, an unrecognised/future code, ...) -- distinct from a code
 * this module recognises as structurally unsupported/not_applicable, because
 * only the latter is safe to tell the developer "retrying will not help". */
const DISCUSSION_ERROR_CODES: Readonly<
  Record<string, { operationResult: DiscussionOperationResult; message: string }>
> = {
  discussion_target_kind_unregistered: {
    operationResult: "unsupported",
    message: "この対象は会話に対応していません。",
  },
  discussion_target_screen_mismatch: {
    operationResult: "unsupported",
    message: "この画面からはこの対象について会話できません。対象の画面から開いてください。",
  },
  discussion_target_scope_mismatch: {
    operationResult: "unsupported",
    message: "この対象は会話の対象にできません。",
  },
  ui_draft_unsupported: {
    operationResult: "unsupported",
    message: "この対象は未保存フォームの下書きの参照に対応していません。",
  },
  prefill_unsupported: {
    operationResult: "unsupported",
    message: "この対象はフォームへの反映に対応していません。既存の画面から直接編集してください。",
  },
  prefill_form_unregistered: {
    operationResult: "not_applicable",
    message: "指定されたフォームはこの対象では使用できません。",
  },
  promotion_unsupported: {
    operationResult: "unsupported",
    message: "この対象は仮説の昇格に対応していません。",
  },
};

export interface DiscussionErrorPresentation {
  operationResult: DiscussionOperationResult;
  message: string;
  /** `true` only for a failure this module cannot explain as structurally
   * unsupported/not_applicable -- i.e. it MAY be transient, so offering a
   * retry is honest. A recognised `unsupported`/`not_applicable` code never
   * sets this: retrying an operation the target structurally cannot do would
   * teach the developer that the button just does not work (#383's rule
   * against a control that never succeeds). */
  retryable: boolean;
}

/** Classify a thrown discussion-adapter request failure for display.
 * Accepts anything error-shaped (an `ApiError`, or a plain object with a
 * `code`) rather than importing `ApiError` itself, so callers in a mocked
 * test environment (where `@/api/client`'s `ApiError` is stubbed) can still
 * exercise this against a plain `{ code }` object. */
export function classifyDiscussionError(err: unknown): DiscussionErrorPresentation {
  const code =
    err !== null && typeof err === "object" && "code" in err
      ? String((err as { code?: unknown }).code ?? "")
      : "";
  const known = DISCUSSION_ERROR_CODES[code];
  if (known) return { ...known, retryable: false };
  return {
    operationResult: "unavailable",
    message: "一時的に取得できませんでした。もう一度お試しください。",
    retryable: true,
  };
}
