import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";
import {
  buildStages,
  loopSearchParams,
  resolveStage,
  type LoopContext,
} from "./model";

// Issue #371: the same progress rail on every surface of the improvement
// loop, so the developer always knows where they are, what the next primary
// action is, and what that action will produce.
//
// The rail navigates; it never executes. Each stage's own page owns its
// primary action, and the human gates (Replay approval, adoption) are
// unchanged — nothing here can approve, adopt, merge, or deploy.

interface ImprovementLoopRailProps extends LoopContext {
  /** Carried into every stage link so the context survives navigation. */
  traceId?: string | null;
  snapshotId?: number | null;
}

export function ImprovementLoopRail(props: ImprovementLoopRailProps) {
  const current = resolveStage(props);
  const stages = buildStages(current);
  const search = loopSearchParams({
    componentId: props.componentId,
    traceId: props.traceId,
    sessionId: props.sessionId,
    snapshotId: props.snapshotId,
  });
  const currentStage = stages.find(s => s.status === "current");

  return (
    <nav
      className="rounded-lg border p-3"
      aria-label="改善ループの現在地"
      data-testid="improvement-loop-rail"
      data-current-stage={current}
    >
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
        {stages.map((stage, index) => (
          <li key={stage.id} className="flex items-center gap-1">
            {index > 0 && (
              <span className="text-muted-foreground" aria-hidden="true">›</span>
            )}
            <Link
              to={`${stage.to}${search}`}
              data-testid={`loop-stage-${stage.id}`}
              data-status={stage.status}
              aria-current={stage.status === "current" ? "step" : undefined}
              className={cn(
                "rounded px-2 py-1 text-xs transition-colors",
                stage.status === "current"
                  ? "bg-secondary font-medium text-foreground"
                  : "text-muted-foreground hover:bg-secondary/50",
              )}
            >
              <span className="mr-1 tabular-nums">{index + 1}.</span>
              {stage.label}
              {/* Colour alone never carries the state (#358 / #363). */}
              {stage.status === "done" && <span className="ml-1">（完了）</span>}
            </Link>
          </li>
        ))}
      </ol>
      {currentStage && (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="loop-stage-produces">
          この段階で作られるもの:{" "}
          <span className="text-foreground">{currentStage.produces}</span>
        </p>
      )}
    </nav>
  );
}
