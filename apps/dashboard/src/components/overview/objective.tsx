import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { OverviewOut } from "@/api/types";
import { CONFIRMATION_VARIANT } from "./display";
import {
  GAP_LIFECYCLE_LABEL, GAP_PRIORITY_BAND_LABEL, MILESTONE_ACHIEVEMENT_LABEL,
  DESIGN_STATUS_LABEL, NEXT_STEP_CTA_LABEL, OBJECTIVE_STATE_LABEL,
  objectiveNextStepHasAction, objectiveNextStepHref,
} from "@/components/product-objective/model";

// Issue #432 (Epic #427) §9.1/§9.3/§9.4: the Overview's ONE Product
// Objective section. Renders `overview.objective` (`OverviewObjectiveOut`)
// verbatim -- Vision, the active Objective, the next Milestone, the primary
// Gap and the single `next_step` all arrive already decided
// (`app/product_objective_projection.py`). This component re-derives
// nothing: it only picks a label for a finite key and a URL for a `next_step`
// key (`components/product-objective/model.ts`), exactly like every other
// Overview card does for its own finite vocabulary (`display.ts`).
//
// §9.4: exactly ONE lead into `/objective-map`, kept SEPARATE from the
// state-dependent `next_step` CTA below (§9.3's own rule for that CTA:
// `waiting`/`unavailable` render no action at all -- the same discipline
// `next-action.tsx`'s `NextActionCard` already holds for the Overview's own
// `next_action`). The header's quiet "Objective Map を見る" link is that one
// lead and is ALWAYS present, in every state, so the card never depends on
// `next_step` to be reachable; the `next_step` block below is a genuinely
// separate question ("what to decide next") that may have no control at all.

export function ObjectiveCard({ overview }: { overview: OverviewOut }) {
  const degraded = overview.degraded_sections.includes("objective");
  const objective = overview.objective;

  const header = (
    <CardHeader className="flex-row items-center justify-between space-y-0">
      <CardTitle as="h2" className="text-lg">目標(Objective)</CardTitle>
      <Link to="/objective-map" className="text-sm text-primary underline" data-testid="overview-objective-map-link">
        Objective Map を見る
      </Link>
    </CardHeader>
  );

  if (degraded || !objective) {
    return (
      <Card data-testid="overview-objective-unavailable">
        {header}
        <CardContent className="text-base text-muted-foreground">
          取得できませんでした。
        </CardContent>
      </Card>
    );
  }

  const hasAction = objectiveNextStepHasAction(objective.next_step_state);

  return (
    <Card data-testid="overview-objective" data-next-step-state={objective.next_step_state}>
      {header}
      <CardContent className="space-y-3">
        {objective.vision ? (
          <div data-testid="overview-objective-vision">
            <p className="text-sm font-medium text-muted-foreground">Vision</p>
            <p className="text-base">{objective.vision.summary}</p>
            <Badge variant={CONFIRMATION_VARIANT[objective.vision.confirmation]} className="mt-1">
              {objective.vision.confirmation_label}
            </Badge>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="overview-objective-vision-unknown">
            Vision はまだ確認されていません。
          </p>
        )}

        {objective.objective_state === null ? (
          <p className="text-base" data-testid="overview-objective-not-started">
            この System にはまだ Product Objective がありません。
          </p>
        ) : objective.active_objective ? (
          <div data-testid="overview-objective-active">
            <p className="text-sm font-medium text-muted-foreground">活性化中の Objective</p>
            <p className="text-base font-medium">{objective.active_objective.title || objective.active_objective.objective_key}</p>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
              <Badge variant="outline">{OBJECTIVE_STATE_LABEL[objective.active_objective.objective_state]}</Badge>
              {objective.active_objective_count > 1 && (
                <span className="text-muted-foreground" data-testid="overview-objective-active-count">
                  他に {objective.active_objective_count - 1} 件
                </span>
              )}
            </div>
          </div>
        ) : (
          <p className="text-base text-muted-foreground" data-testid="overview-objective-no-active">
            活性化された Objective はありません。
          </p>
        )}

        {objective.next_milestone && (
          <div data-testid="overview-objective-next-milestone">
            <p className="text-sm font-medium text-muted-foreground">次の Milestone</p>
            <p className="text-base">{objective.next_milestone.title || objective.next_milestone.milestone_key}</p>
            <div className="mt-1 flex flex-wrap gap-2 text-sm">
              <Badge variant="outline">{DESIGN_STATUS_LABEL[objective.next_milestone.design_status]}</Badge>
              <Badge variant="outline">{MILESTONE_ACHIEVEMENT_LABEL[objective.next_milestone.achievement]}</Badge>
            </div>
          </div>
        )}

        {objective.primary_gap && (
          <div data-testid="overview-objective-primary-gap">
            <p className="text-sm font-medium text-muted-foreground">主な Gap</p>
            <p className="text-base">{objective.primary_gap.title || objective.primary_gap.gap_key}</p>
            <div className="mt-1 flex flex-wrap gap-2 text-sm">
              <Badge variant="outline">{GAP_LIFECYCLE_LABEL[objective.primary_gap.lifecycle]}</Badge>
              {objective.primary_gap.priority_band !== "unset" && (
                <Badge variant="secondary">{GAP_PRIORITY_BAND_LABEL[objective.primary_gap.priority_band]}</Badge>
              )}
            </div>
          </div>
        )}

        <div className="space-y-1.5 border-t pt-3">
          <p className="text-sm font-medium text-muted-foreground">次に決めること</p>
          {/* §9.3: `waiting`/`unavailable` render NO control at all -- only
              the server's own sentence, exactly like `NextActionCard`'s
              `overview-next-action-waiting` / `-unavailable` branches. A
              `complete` row (`next_step: "none"`) has an empty server reason
              (`_decide_next_step` row 15), so this is the one place a fixed
              fallback sentence is used, never a fabricated reason. */}
          {hasAction ? (
            <>
              <Link
                to={objectiveNextStepHref(objective, overview.interview_session_id)}
                className="inline-flex items-center rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
                data-testid="overview-objective-cta"
                data-next-step-key={objective.next_step}
              >
                {NEXT_STEP_CTA_LABEL[objective.next_step]}
              </Link>
              {objective.next_step_reason && (
                <p className="text-base" data-testid="overview-objective-next-step-reason">
                  {objective.next_step_reason}
                </p>
              )}
              {objective.next_step_completion && (
                <p className="text-sm text-muted-foreground" data-testid="overview-objective-next-step-completion">
                  完了条件: {objective.next_step_completion}
                </p>
              )}
              {objective.next_step_value && (
                <p className="text-sm text-muted-foreground" data-testid="overview-objective-next-step-value">
                  完了すると: {objective.next_step_value}
                </p>
              )}
            </>
          ) : objective.next_step_state === "complete" ? (
            <p className="text-base" data-testid="overview-objective-next-step-complete">
              {objective.next_step_reason || "現在、次に決めるべきことはありません。"}
            </p>
          ) : (
            <p className="text-base" data-testid="overview-objective-next-step-waiting">
              {objective.next_step_reason || "状態を判定できなかったため、次の操作を提示できません。"}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
