// The single DiscussionAdapter registry (Issue #444, Epic #443 Phase 1).
//
// docs/ai-discussion-adapter.md §1.5/§1.6 is the canonical contract. Before
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
// `forms` stays `[]` for every adapter through Phase 1-4 (#445/#446 populate
// it later); `invalidateKeys` and `deepLink` ARE implemented now, per
// docs/ai-discussion-adapter.md's Phase 1 scope note -- they are read-only
// conveniences (which caches to refresh, which URL opens the target) that do
// not depend on the unsaved-draft or prefill machinery those later phases add.

import type {
  AssistantDiscussionTargetIn,
  DiscussionScope,
  DiscussionTargetKind,
} from "@/api/types";
import { sysKey } from "@/api/hooks";

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
   * own `label` (docs/ai-discussion-adapter.md §1.4). Established
   * product-concept terms (Journey, Requirement, Solution Design) stay
   * English per CLAUDE.md's Dashboard UI言語規約 initial-mention rule. */
  label: string;
  /** Resolve the most specific selectable target from the URL + screen id.
   * `null` = nothing more specific than "the whole screen" is selected. */
  resolveFromRoute(screenId: string, params: URLSearchParams): DiscussionCandidate | null;
  /** Prefill destinations (Issue #446). Empty through Phase 1-4. */
  forms: readonly UiDraftFormBinding[];
  /** React Query key PREFIXES to invalidate after a canonical write against
   * this target (`invalidateQueries` matches by prefix, so a prefix missing
   * the target_ref still invalidates every variant of that query). */
  invalidateKeys(targetRef: string): readonly (readonly unknown[])[];
  /** The URL that opens this target on its (primary) screen. Navigates --
   * never executes (docs/ai-discussion-adapter.md §3.6 / #358 / #427's CTA
   * rule carried over from the parent Epic). `null` when no screen can
   * plausibly render this ref. */
  deepLink(targetRef: string): string | null;
}

const SCREEN_PATH: Record<string, string> = {
  overview: "/",
  interview: "/interview",
  "ux-design-studio": "/ux-design-studio",
  "journey-blueprint": "/journey-blueprint",
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
  forms: [],
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
  forms: [],
  invalidateKeys: (targetRef) => [
    sysKey("ux-journeys"),
    [...sysKey("ux-journey"), journeyKeyOf(targetRef)],
  ],
  deepLink: (targetRef) =>
    `/ux-design-studio?tab=journeys&journey=${encodeURIComponent(journeyKeyOf(targetRef))}`,
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
  forms: [],
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
  forms: [],
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
  forms: [],
  invalidateKeys: (targetRef) => {
    const journeyKey = journeyKeyOf(targetRef);
    return [
      [...sysKey("journey-blueprint"), journeyKey],
      [...sysKey("journey-blueprint-diff"), journeyKey],
    ];
  },
  deepLink: (targetRef) => `/journey-blueprint?journey=${encodeURIComponent(journeyKeyOf(targetRef))}`,
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
};

/**
 * Per-screen candidate priority (docs/ai-discussion-adapter.md §1.5: "a
 * fixed priority within one adapter set -- keep the existing precedence
 * exactly"). This is the ONLY place screen identity governs which adapter
 * wins -- `assistant-panel.tsx` itself never branches on `screenId` for
 * discussion purposes. Order is most-specific-first, matching the old
 * per-screen `if` chain in `deriveDiscussionCandidate` exactly:
 *   - ux-design-studio: step > requirement > design > journey
 *   - journey-blueprint: lane cell > step > journey
 */
const CANDIDATE_PRIORITY: Record<string, readonly DiscussionTargetKind[]> = {
  interview: ["interview_session"],
  "ux-design-studio": ["ux_journey_step", "ux_requirement", "solution_design", "ux_journey"],
  "journey-blueprint": ["blueprint_lane_cell", "ux_journey_step", "ux_journey"],
  overview: [],
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
