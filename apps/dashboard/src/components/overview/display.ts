import type {
  OverviewFindingSeverity,
  OverviewFindingStatus,
  OverviewLoopStageStatus,
  OverviewTargetOut,
  UnderstandingConfirmationState,
  UnderstandingReadinessState,
} from "@/api/types";

// Issue #384: the Overview's DISPLAY tables, and nothing else.
//
// This file is deliberately thin. Every semantic decision the screen renders —
// the readiness verdict, a finding's importance, its `new` / `ongoing` /
// `not_compared` status, the single next action, the loop position — is
// already decided by `GET /overview` and arrives with its own Japanese label.
// Re-deciding any of it here is exactly what #380 principle 6 forbids, and it
// is what let the old Overview show a green 受信中 for a system that had been
// silent for two weeks.
//
// What is left is genuinely presentational: which badge variant a finite value
// maps to, and the query string a target turns into. Both are safe to change
// without changing what the screen asserts.

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

/**
 * Severity → badge variant.
 *
 * Colour is never the only carrier: every badge also renders the server's
 * `severity_label` text, so the state survives greyscale, colour-blindness and
 * a screen reader (#384 アクセシビリティ).
 */
export const FINDING_SEVERITY_VARIANT: Record<OverviewFindingSeverity, BadgeVariant> = {
  blocker: "destructive",
  human_decision_required: "warning",
  material_change: "default",
  informative: "secondary",
};

export const FINDING_STATUS_VARIANT: Record<OverviewFindingStatus, BadgeVariant> = {
  new: "default",
  ongoing: "secondary",
  not_compared: "outline",
};

export const READINESS_VARIANT: Record<UnderstandingReadinessState, BadgeVariant> = {
  not_built: "secondary",
  building: "secondary",
  needs_confirmation: "warning",
  ready: "success",
  recheck_required: "warning",
  blocked: "destructive",
};

export const CONFIRMATION_VARIANT: Record<UnderstandingConfirmationState, BadgeVariant> = {
  confirmed: "success",
  ai_hypothesis: "secondary",
  conflicting: "destructive",
  unknown: "outline",
  recheck_required: "warning",
};

export const LOOP_STATUS_LABEL: Record<OverviewLoopStageStatus, string> = {
  reached: "到達済み",
  current: "現在地",
  future: "これから",
};

/**
 * A target's route plus its own query parameters.
 *
 * The server already emits each destination's OWN parameter names (the #371
 * rule that stopped links from navigating and then arriving with nothing
 * selected), so this only has to serialise them.
 */
export function targetHref(target: OverviewTargetOut | null | undefined): string {
  if (!target) return "#";
  const params = new URLSearchParams(target.params ?? {});
  const query = params.toString();
  const hash = target.anchor ? `#${target.anchor}` : "";
  return `${target.route}${query ? `?${query}` : ""}${hash}`;
}
