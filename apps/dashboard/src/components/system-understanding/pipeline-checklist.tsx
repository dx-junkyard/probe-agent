import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DiagnosticCheckCard, EnvFixDialog } from "@/components/diagnostics-badge";
import { useDiagnosticActivate } from "@/components/diagnostic-fix";
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

export function PipelineChecklist({ steps, checksByStep }: {
  steps: SystemUnderstandingPipelineStep[];
  checksByStep: Record<string, SystemDiagnosticCheck[]>;
}) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const { activate, envCheck, closeEnv } = useDiagnosticActivate();
  return (
    <>
    <ul className="space-y-2" data-testid="pipeline-checklist">
      {steps.map((s) => {
        const link = STEP_LINKS[s.step];
        const label = STEP_LABELS[s.step] ?? s.step;
        const relatedChecks = s.status === "complete" ? [] : (checksByStep[s.step] ?? []);
        const expanded = expandedStep === s.step;
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
