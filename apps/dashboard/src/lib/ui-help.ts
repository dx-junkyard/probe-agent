// UI 機能解説モード (Issue #440, Epic #436).
//
// TypeScript mirror of `apps/control-server/app/ui_help_registry.py`'s
// finite vocabularies, plus `HELP_IDS`: every `data-help-id` the Dashboard
// actually renders. `test_ui_help_registry.py`'s parity test parses this
// file and asserts the two sides list exactly the same ids -- the same
// discipline `test_interview_type_parity.py` already applies to shared
// response contracts, applied here to the help-mode id space instead.
//
// This file holds NO explanation text -- the text lives only in the
// server registry (single source of truth, never duplicated client-side).

export const UI_HELP_SCOPES = ["screen", "section", "element"] as const;
export type UiHelpScope = (typeof UI_HELP_SCOPES)[number];

export const UI_HELP_ACTION_KINDS = ["navigate", "configure", "operate"] as const;
export type UiHelpActionKind = (typeof UI_HELP_ACTION_KINDS)[number];

// Every `data-help-id` value rendered anywhere in the Dashboard. Keep this
// list and the Python `UI_HELP_ENTRIES` registry in exact 1:1 correspondence
// -- adding a `data-help-id` to a screen without a matching registry entry
// (or vice versa) fails `test_ui_help_registry.py`.
export const HELP_IDS = [
  // Overview
  "overview",
  "overview.header",
  "overview.purpose_frame",
  "overview.purpose_frame.question",
  "overview.brief",
  "overview.brief.vision",
  "overview.brief.system_purpose",
  "overview.brief.capabilities",
  "overview.findings",
  "overview.next_action",
  "overview.loop_rail",
  "overview.objective",
  "overview.runtime_health",

  // Interview
  "interview",
  "interview.session_selector",
  "interview.workflow_state",
  "interview.status_summary",
  "interview.exceptions",
  "interview.work_surface",
  "interview.brief",
  "interview.unresolved_items",
  "interview.qa_progress",
  "interview.understanding_map",
  "interview.detail_pane",
  "interview.auxiliary_panel",

  // UX Design Studio
  "ux-design-studio",
  "ux-design-studio.next_decision",
  "ux-design-studio.journey.list",
  "ux-design-studio.journey.detail",
  "ux-design-studio.journey.baseline",
  "ux-design-studio.requirement.list",
  "ux-design-studio.requirement.detail",
  "ux-design-studio.solution_design.list",
  "ux-design-studio.solution_design.detail",
  "ux-design-studio.solution_design.handoff",
  "ux-design-studio.solution_design.evaluation",

  // Journey Blueprint
  "journey-blueprint",
  "journey-blueprint.journey_select",
  "journey-blueprint.view_toggle",
  "journey-blueprint.lane.stakeholder_action",
  "journey-blueprint.lane.touchpoint",
  "journey-blueprint.lane.frontstage",
  "journey-blueprint.lane.backstage",
  "journey-blueprint.lane.support",
  "journey-blueprint.lane.external",
  "journey-blueprint.lane.requirement",
  "journey-blueprint.lane.evidence",
  "journey-blueprint.lane.failure_recovery",
  "journey-blueprint.detail_pane",
  "journey-blueprint.diff",
] as const;

export type HelpId = (typeof HELP_IDS)[number];

/** The four discussion/help-mode screens (Epic #436 §0 / §1.1). */
export const HELP_MODE_SCREEN_IDS = [
  "overview",
  "interview",
  "ux-design-studio",
  "journey-blueprint",
] as const;
export type HelpModeScreenId = (typeof HELP_MODE_SCREEN_IDS)[number];
