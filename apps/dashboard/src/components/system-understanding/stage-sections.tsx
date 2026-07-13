import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { ExternalLink } from "lucide-react";
import type { NextActionCategory } from "@/api/types";

// Issue #179: System Understanding is reorganized into 4 fixed stages that
// mirror the product vision (System Understanding -> Capability Map -> Flow
// Explorer -> Probe Planner -> Experiment Workspace). The stage a page or a
// Next Action belongs to is an explicit finite mapping (CLAUDE.md Principle
// 6) — no reasoning model involved.
export type Stage = NextActionCategory;

export const STAGES: Stage[] = ["understand", "observe", "instrument", "evaluate"];

export const STAGE_LABELS: Record<Stage, string> = {
  understand: "Understand",
  observe: "Decide Where to Observe",
  instrument: "Instrument",
  evaluate: "Evaluate",
};

export const STAGE_DESCRIPTIONS: Record<Stage, string> = {
  understand: "Docs, symbols, capabilities, and docs-code gaps discovered from the repository.",
  observe: "Entrypoints and flows worth watching before choosing where to instrument.",
  instrument: "Probe plans, patches, and validation state for the observed entrypoints.",
  evaluate: "Traces, experiments, and the decisions recorded from them.",
};

// Issue #202: finite stage completion status, badged next to each stage
// heading. Derived server-side (system_understanding_service.
// _derive_stage_statuses) -- purely a display mapping here, no logic.
export type StageStatusValue = "not_started" | "in_progress" | "blocked" | "complete";

export const STAGE_STATUS_LABELS: Record<StageStatusValue, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  blocked: "Blocked",
  complete: "Complete",
};

export const STAGE_STATUS_BADGE_VARIANT: Record<
  StageStatusValue,
  "outline" | "secondary" | "destructive" | "success"
> = {
  not_started: "outline",
  in_progress: "secondary",
  blocked: "destructive",
  complete: "success",
};

// Detail pages linked from each stage. Existing routes only — Issue #179 does
// not add or change routing.
export const STAGE_DETAIL_LINKS: Record<Stage, { to: string; label: string }[]> = {
  understand: [
    { to: "/capability-map", label: "Capability Map" },
    { to: "/interview", label: "Interview" },
    { to: "/feature-map", label: "Feature Map" },
    { to: "/repository", label: "Repository" },
  ],
  observe: [
    { to: "/flow-explorer", label: "Flow Explorer" },
    { to: "/interview", label: "Interview" },
    { to: "/trace-lineage", label: "Trace Lineage" },
  ],
  instrument: [
    { to: "/probe-planner", label: "Probe Planner" },
  ],
  evaluate: [
    { to: "/experiments", label: "Experiments" },
    { to: "/trace-analyzers", label: "Trace Analyzers" },
  ],
};

export function StageSection({ stage, index, status, counts, children }: {
  stage: Stage;
  index: number;
  // Issue #202: deterministic completion status/counts for this stage. Both
  // optional so callers/fixtures that predate stage status keep rendering
  // (no badge, no counts line) instead of breaking.
  status?: string;
  counts?: Record<string, number>;
  children: ReactNode;
}) {
  const links = STAGE_DETAIL_LINKS[stage];
  const statusLabel = status ? STAGE_STATUS_LABELS[status as StageStatusValue] : undefined;
  const statusVariant = status ? STAGE_STATUS_BADGE_VARIANT[status as StageStatusValue] : undefined;
  const countEntries = counts ? Object.entries(counts) : [];
  return (
    <section className="space-y-4" data-testid={`stage-section-${stage}`}>
      <div className="flex items-baseline gap-2">
        <Badge variant="secondary" className="text-xs shrink-0">{index}</Badge>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold" data-testid={`stage-title-${stage}`}>
              {STAGE_LABELS[stage]}
            </h2>
            {status && (
              <Badge
                variant={statusVariant ?? "outline"}
                className="text-xs"
                data-testid={`stage-status-${stage}`}
              >
                {statusLabel ?? status}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">{STAGE_DESCRIPTIONS[stage]}</p>
          {countEntries.length > 0 && (
            <p
              className="text-xs text-muted-foreground mt-0.5"
              data-testid={`stage-counts-${stage}`}
            >
              {countEntries.map(([k, v]) => `${k}: ${v}`).join(" · ")}
            </p>
          )}
        </div>
      </div>

      <div className="space-y-4">{children}</div>

      {links.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs" data-testid={`stage-links-${stage}`}>
          <span className="text-muted-foreground">Related pages:</span>
          {links.map((l) => (
            <Link
              key={l.to}
              to={l.to}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-primary hover:underline"
            >
              {l.label}
              <ExternalLink className="h-3 w-3" />
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
