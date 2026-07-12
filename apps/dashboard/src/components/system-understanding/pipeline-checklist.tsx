import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { DiagnosticCheckCard, EnvFixDialog } from "@/components/diagnostics-badge";
import { useDiagnosticActivate } from "@/components/diagnostic-fix";
import { cn } from "@/lib/utils";
import {
  CheckCircle2, XCircle, AlertTriangle, Ban, HelpCircle, Stethoscope,
} from "lucide-react";
import type {
  SystemDiagnosticCheck,
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
 * Issue #200: fixed step -> CTA mapping for the "next single action" hint on
 * the first incomplete pipeline step. This is a deterministic, explicitly
 * enumerated mapping (CLAUDE.md Principle 6) — not an inferred suggestion.
 * `repository_configured` / `snapshot_ready` link to the Repository page
 * where the repo/snapshot is set up; every other known step is resolved by
 * running the Build / Refresh job, so its CTA connects to that action.
 */
type StepCta = { kind: "repository" | "build" | "interview"; label: string };

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

export function PipelineChecklist({ steps, checksByStep, onRunBuild, buildDisabled }: {
  steps: SystemUnderstandingPipelineStep[];
  checksByStep: Record<string, SystemDiagnosticCheck[]>;
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
        const isEmptyCapabilityHierarchy = (
          s.step === "capability_hierarchy_ready"
          && s.status === "warning"
          && /no capabilities|0 capabilities|capability が 0/i.test(s.detail ?? "")
        );
        const cta = s.step === firstIncompleteStep
          ? (isEmptyCapabilityHierarchy
            ? { kind: "interview" as const, label: "Review interview proposals" }
            : STEP_CTA[s.step])
          : undefined;
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
              {cta && cta.kind === "repository" && (
                <Link
                  to="/repository"
                  data-testid={`pipeline-cta-${s.step}`}
                  className={cn(buttonVariants({ size: "sm" }), "h-7 text-xs")}
                >
                  {cta.label}
                </Link>
              )}
              {cta && cta.kind === "build" && (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  data-testid={`pipeline-cta-${s.step}`}
                  onClick={onRunBuild}
                  disabled={buildDisabled}
                >
                  {cta.label}
                </Button>
              )}
              {cta && cta.kind === "interview" && (
                <Link
                  to="/interview"
                  data-testid={`pipeline-cta-${s.step}`}
                  className={cn(buttonVariants({ size: "sm" }), "h-7 text-xs")}
                >
                  {cta.label}
                </Link>
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
