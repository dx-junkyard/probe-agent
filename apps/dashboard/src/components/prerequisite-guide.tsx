import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useSystemState } from "@/api/hooks";
import {
  USER_PHASE_LABELS,
  systemStateTarget,
} from "@/components/system-state";

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
 * authors state copy here. When the System reaches the terminal `diagnosis`
 * phase, every prerequisite is met and the guide renders nothing — so it
 * disappears automatically as the phase advances (Issue #241 acceptance).
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

  // No server phase (older Control Server or not loaded), or all
  // prerequisites met (terminal phase): render nothing.
  if (!data?.user_phase || data.user_phase === "diagnosis") return null;

  const phaseLabel = USER_PHASE_LABELS[data.user_phase] ?? data.user_phase;
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
