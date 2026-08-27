import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useSystemState } from "@/api/hooks";
import {
  USER_PHASE_LABELS,
  systemStateTarget,
} from "@/components/system-state";
import type { UserPhase } from "@/api/types";

/**
 * Phases this guide has authored copy/behavior for (Issue #241). It only
 * ever renders the generic "current phase label + phase-scoped
 * `primary_item`" shape, and its three call sites (Overview's zero-component
 * state, Feature Map's empty-features state, and the Probe Planner generate
 * dialog gated on `phases.preparation`) are all about steering a System
 * through initial setup/analysis before components/features/probe plans
 * exist. Once preparation is done, those preconditions are already
 * satisfied, so the later 6-step-flow phases (Issue #256:
 * instrumentation / observation / evaluation / publish) are out of this
 * guide's scope -- rendering it there would show stale "get started" copy
 * for a System that has already started. A deterministic key lookup against
 * this finite set (not a single terminal-phase check) is what lets this
 * component stay well-defined as PHASE_ORDER grows, without needing to
 * author new guide copy for the later phases here (that guidance UX belongs
 * to sibling sub-issues #257/#258).
 */
const SUPPORTED_PREREQUISITE_PHASES: readonly UserPhase[] = ["setup", "preparation"];

/**
 * Issue #241: a phase-driven prerequisite guide shared by every screen that
 * can be empty because an earlier journey stage is not done yet (Overview,
 * Feature Map, Probe Planner). It answers "why is this empty and where do I
 * go next" from the canonical `GET /system-state` projection alone:
 *
 * - the current `user_phase` names how far the System has progressed, and
 * - the phase-scoped `primary_item` (already the top actionable StateItem
 *   for the current phase, server-computed and phase-suppressed in #237/#239)
 *   supplies the "next step" copy and its navigation target.
 *
 * All state-derived copy (summary / remediation / action_label) comes from
 * the server message catalog (#240); the client never derives a phase or
 * authors state copy here. Once `user_phase` moves past `preparation` (Issue
 * #256's instrumentation / observation / evaluation / publish phases), every
 * setup/preparation prerequisite is by definition met, so the guide renders
 * nothing (see `SUPPORTED_PREREQUISITE_PHASES` above) -- it disappears
 * automatically as the phase advances (Issue #241 acceptance).
 */
export function PrerequisiteGuide({
  testId = "prerequisite-guide",
  className,
}: {
  testId?: string;
  className?: string;
}) {
  const { data } = useSystemState();
  const navigate = useNavigate();

  // No server phase (older Control Server or not loaded), or the current
  // phase is not one this guide has copy/behavior for: render nothing.
  if (!data?.user_phase || !SUPPORTED_PREREQUISITE_PHASES.includes(data.user_phase)) return null;

  // Issue #240: prefer the server-provided label for the current phase
  // (from the matching `phases` entry), falling back to the fixed
  // client-side map, then the raw token.
  const phaseLabel = data.phases?.find((p) => p.phase === data.user_phase)?.label
    ?? USER_PHASE_LABELS[data.user_phase]
    ?? data.user_phase;
  const item = data.primary_item ?? null;
  const target = item ? systemStateTarget(item) : null;

  return (
    <div
      data-testid={testId}
      data-current-phase={data.user_phase}
      className={
        className ??
        "mx-auto max-w-md space-y-2 rounded-lg border bg-muted/30 px-4 py-3 text-sm"
      }
    >
      <p className="text-xs font-medium text-muted-foreground" data-testid={`${testId}-phase`}>
        現在のフェーズ: {phaseLabel}
      </p>
      {item ? (
        <>
          <p className="font-medium">{item.summary}</p>
          {item.remediation && (
            <p className="text-xs text-muted-foreground">{item.remediation}</p>
          )}
          {target && (
            <Button
              size="sm"
              onClick={() => navigate(target)}
              data-testid={`${testId}-cta`}
            >
              {item.target_ui?.action_label || "次の操作へ進む"}
            </Button>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          このフェーズを完了するための操作を進めてください。
        </p>
      )}
    </div>
  );
}
