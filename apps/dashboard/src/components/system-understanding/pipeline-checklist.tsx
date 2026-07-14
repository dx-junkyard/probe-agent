import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { DiagnosticCheckCard, EnvFixDialog } from "@/components/diagnostics-badge";
import { useDiagnosticActivate } from "@/components/diagnostic-fix";
import { systemStateTarget } from "@/components/system-state";
import { cn } from "@/lib/utils";
import {
  CheckCircle2, XCircle, AlertTriangle, Ban, HelpCircle, Stethoscope,
} from "lucide-react";
import type {
  SystemDiagnosticCheck,
  SystemStateItem,
  SystemUnderstandingPipelineStep,
} from "@/api/types";

export const STEP_LABELS: Record<string, string> = {
  repository_configured: "Repository configured",
  snapshot_ready: "Snapshot ready",
  documentation_indexed: "Documentation indexed",
  documentation_claims_scanned: "Documentation claims scanned",
  symbols_indexed: "Code symbols indexed",
  entrypoints_discovered: "Entrypoints discovered",
  docs_code_reconciled: "Docs-code reconciled",
  capability_hierarchy_ready: "Capability hierarchy ready",
};

const STEP_LINKS: Record<string, string> = {
  repository_configured: "/repository",
  snapshot_ready: "/repository",
  symbols_indexed: "/repository",
  entrypoints_discovered: "/flow-explorer",
  capability_hierarchy_ready: "/capability-map",
  docs_code_reconciled: "/system-understanding",
};

/**
 * Issue #200 introduced a fixed step -> CTA mapping for the "next single
 * action" hint on the first incomplete pipeline step. Issue #239 replaced it
 * as the primary source: the CTA now comes from the `SystemStateItem` whose
 * `related_pipeline_steps` names this step (`stateItemForStep` below), reusing
 * the same `target_ui` / `systemStateTarget()` every other notification
 * surface (badge, banner, notice) already consumes — so the same root cause
 * never shows different text or a different destination on this page vs.
 * elsewhere (CLAUDE.md Principle 6: the StateItem's fields are themselves a
 * finite, server-computed set; nothing is inferred here).
 *
 * This fixed map is kept only as the last-resort fallback for a step that
 * has no matching StateItem (as of #239, only `repository_configured` in the
 * "repository not configured for this System" case: its native item,
 * `repository.configuration.missing`, does not carry `related_pipeline_steps`,
 * and the diagnostic checks that do are suppressed as already-covered
 * duplicates — see docs/system-understanding-navigation.md).
 */
type StepCta = { kind: "repository" | "build"; label: string };

const STEP_CTA: Record<string, StepCta> = {
  repository_configured: { kind: "repository", label: "Configure repository" },
  snapshot_ready: { kind: "repository", label: "Create snapshot" },
  symbols_indexed: { kind: "build", label: "Run Build / Refresh" },
  documentation_indexed: { kind: "build", label: "Run Build / Refresh" },
  documentation_claims_scanned: { kind: "build", label: "Run Build / Refresh" },
  entrypoints_discovered: { kind: "build", label: "Run Build / Refresh" },
  docs_code_reconciled: { kind: "build", label: "Run Build / Refresh" },
  capability_hierarchy_ready: { kind: "build", label: "Run Build / Refresh" },
};

/**
 * The first (highest-priority; `pageItems` arrives pre-sorted by the server)
 * StateItem whose `related_pipeline_steps` names this pipeline step.
 */
function stateItemForStep(items: SystemStateItem[], step: string): SystemStateItem | undefined {
  return items.find((item) => item.related_pipeline_steps.includes(step));
}

export function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "complete":
      return <CheckCircle2 className="h-4 w-4 text-green-600" />;
    case "warning":
      return <AlertTriangle className="h-4 w-4 text-yellow-600" />;
    case "failed":
      return <XCircle className="h-4 w-4 text-red-600" />;
    case "blocked":
      return <Ban className="h-4 w-4 text-orange-500" />;
    default:
      return <HelpCircle className="h-4 w-4 text-muted-foreground" />;
  }
}

export function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "complete": return "default";
    case "warning": return "secondary";
    case "failed": return "destructive";
    case "blocked": return "outline";
    default: return "outline";
  }
}

export function PipelineChecklist({ steps, checksByStep, pageItems, onRunBuild, buildDisabled }: {
  steps: SystemUnderstandingPipelineStep[];
  checksByStep: Record<string, SystemDiagnosticCheck[]>;
  /** Issue #239: `GET /system-state`'s `page_items["/system-understanding"]`,
   * already phase-scoped and deduped by the server. Used to drive the CTA on
   * the first incomplete step (`stateItemForStep`); falls back to `STEP_CTA`
   * only when no item matches this step. */
  pageItems?: SystemStateItem[];
  /** Issue #200: runs the Build / Refresh job, wired to the first incomplete
   * step's CTA when that step's fix is "run a build" rather than "go
   * configure the repository". */
  onRunBuild?: () => void;
  /** Disables the build CTA while a build is already running/pending. */
  buildDisabled?: boolean;
}) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const { activate, envCheck, closeEnv } = useDiagnosticActivate();

  // Issue #200: the CTA appears on the first non-complete step only (array
  // order, deterministic), so onboarding always highlights exactly one
  // "next action" instead of a wall of steps.
  const firstIncompleteStep = steps.find((s) => s.status !== "complete")?.step ?? null;

  return (
    <>
    <ul className="space-y-2" data-testid="pipeline-checklist">
      {steps.map((s) => {
        const link = STEP_LINKS[s.step];
        const label = STEP_LABELS[s.step] ?? s.step;
        const relatedChecks = s.status === "complete" ? [] : (checksByStep[s.step] ?? []);
        const expanded = expandedStep === s.step;
        const isFirstIncomplete = s.step === firstIncompleteStep;
        // Issue #239: the CTA's text and destination come from the matching
        // StateItem (same source the badge/banner/notice already read), not
        // a hardcoded per-step label. `user_action_kind === "build"` is the
        // one finite signal that means "trigger Build / Refresh directly"
        // rather than "navigate somewhere" -- every other kind (confirm,
        // review, rerun, configure, inspect, ...) renders as a link to
        // target_ui, even when that link happens to be this same page (it
        // still highlights the right control via the diagnostic-focus
        // query params).
        const stateItem = isFirstIncomplete ? stateItemForStep(pageItems ?? [], s.step) : undefined;
        const fallbackCta = isFirstIncomplete && !stateItem ? STEP_CTA[s.step] : undefined;
        return (
          <li key={s.step} className="text-sm">
            <div className="flex items-center gap-3">
              <StatusIcon status={s.status} />
              <span className="flex-1">
                {link ? (
                  <Link to={link} className="hover:underline">{label}</Link>
                ) : (
                  label
                )}
              </span>
              <Badge variant={statusVariant(s.status)} className="text-xs">
                {s.status}
              </Badge>
              {s.detail && (
                <span className="text-xs text-muted-foreground ml-1">{s.detail}</span>
              )}
              {stateItem && stateItem.user_action_kind === "build" && (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  data-testid={`pipeline-cta-${s.step}`}
                  onClick={onRunBuild}
                  disabled={buildDisabled}
                >
                  {stateItem.target_ui?.action_label || "Run Build / Refresh"}
                </Button>
              )}
              {stateItem && stateItem.user_action_kind !== "build" && (
                <Link
                  to={systemStateTarget(stateItem) ?? stateItem.target_ui?.route ?? "#"}
                  data-testid={`pipeline-cta-${s.step}`}
                  className={cn(buttonVariants({ size: "sm" }), "h-7 text-xs")}
                >
                  {stateItem.target_ui?.action_label || "対応する"}
                </Link>
              )}
              {fallbackCta && fallbackCta.kind === "repository" && (
                <Link
                  to="/repository"
                  data-testid={`pipeline-cta-${s.step}`}
                  className={cn(buttonVariants({ size: "sm" }), "h-7 text-xs")}
                >
                  {fallbackCta.label}
                </Link>
              )}
              {fallbackCta && fallbackCta.kind === "build" && (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  data-testid={`pipeline-cta-${s.step}`}
                  onClick={onRunBuild}
                  disabled={buildDisabled}
                >
                  {fallbackCta.label}
                </Button>
              )}
              {relatedChecks.length > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 text-xs gap-1"
                  data-testid={`pipeline-diagnose-${s.step}`}
                  onClick={() => setExpandedStep(expanded ? null : s.step)}
                >
                  <Stethoscope className="h-3 w-3" />
                  Why? ({relatedChecks.length})
                </Button>
              )}
            </div>
            {expanded && relatedChecks.length > 0 && (
              <div className="mt-2 ml-7 space-y-2" data-testid={`pipeline-diagnostics-${s.step}`}>
                {relatedChecks.map((c) => (
                  <DiagnosticCheckCard key={c.check_id} check={c} onActivate={activate} />
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ul>
    <EnvFixDialog check={envCheck} onClose={closeEnv} />
    </>
  );
}
