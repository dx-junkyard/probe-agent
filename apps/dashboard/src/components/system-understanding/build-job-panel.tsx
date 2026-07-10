import {
  useRetrySystemUnderstandingJob,
  useCancelSystemUnderstandingJob,
  useCancelSystemUnderstandingStep,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  CheckCircle2, XCircle, Ban, HelpCircle, RefreshCw,
} from "lucide-react";
import { statusVariant } from "@/components/system-understanding/pipeline-checklist";
import type {
  SystemUnderstandingBuildOut,
  SystemUnderstandingBuildStep,
} from "@/api/types";

// Build job step names (Issue #109) differ from the artifact pipeline shown
// in PipelineChecklist.
const JOB_STEP_LABELS: Record<string, string> = {
  symbol_index: "Symbol index",
  entrypoint_index: "Entrypoint discovery",
  documentation_index: "Documentation index",
  claim_scan: "Documentation claim scan (LLM)",
  understanding_graph: "Understanding graph",
  docs_code_reconcile: "Docs-code reconcile",
  capability_hierarchy: "Capability hierarchy",
};

export const TERMINAL_JOB_STATUSES = ["completed", "partial", "failed", "cancelled"];

function JobStepIcon({ status }: { status: string }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-4 w-4 text-green-600" />;
    case "running":
      return <RefreshCw className="h-4 w-4 animate-spin text-blue-500" />;
    case "failed":
      return <XCircle className="h-4 w-4 text-red-600" />;
    case "blocked":
      return <Ban className="h-4 w-4 text-orange-500" />;
    case "cancelled":
      return <XCircle className="h-4 w-4 text-muted-foreground" />;
    default:
      return <HelpCircle className="h-4 w-4 text-muted-foreground" />;
  }
}

function formatDuration(ms: number | null): string | null {
  if (ms == null) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function jobStatusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "completed": return "default";
    case "running": case "queued": return "secondary";
    case "failed": case "partial": return "destructive";
    default: return "outline";
  }
}

function JobStepRow({ job, step, active }: {
  job: SystemUnderstandingBuildOut;
  step: SystemUnderstandingBuildStep;
  active: boolean;
}) {
  const retryStep = useRetrySystemUnderstandingJob();
  const cancelStep = useCancelSystemUnderstandingStep();
  const label = JOB_STEP_LABELS[step.step] ?? step.step;
  const duration = formatDuration(step.duration_ms);
  const isClaimScan = step.step === "claim_scan";
  const tasks = job.llm_tasks;
  const retryable = ["failed", "blocked", "cancelled"].includes(step.status) && !active;
  const cancellable = ["pending", "running"].includes(step.status) && active;

  return (
    <li className="text-sm" data-testid={`job-step-${step.step}`}>
      <div className="flex items-center gap-3">
        <JobStepIcon status={step.status} />
        <span className="flex-1">
          {label}
          {step.reused_existing && (
            <Badge variant="outline" className="ml-2 text-xs">reused</Badge>
          )}
        </span>
        {isClaimScan && tasks && tasks.total > 0 && (
          <span className="text-xs text-muted-foreground" data-testid="job-llm-progress">
            chunks {tasks.completed}/{tasks.total}
            {tasks.failed > 0 && ` (${tasks.failed} failed)`}
          </span>
        )}
        {duration && <span className="text-xs text-muted-foreground">{duration}</span>}
        <Badge variant={statusVariant(step.status === "pending" ? "missing" : step.status)} className="text-xs">
          {step.status}
        </Badge>
        {retryable && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-xs"
            data-testid={`job-step-retry-${step.step}`}
            disabled={retryStep.isPending}
            onClick={() => retryStep.mutate({ jobId: job.id, step: step.step })}
          >
            Retry
          </Button>
        )}
        {cancellable && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-xs"
            data-testid={`job-step-cancel-${step.step}`}
            disabled={cancelStep.isPending}
            onClick={() => cancelStep.mutate({ jobId: job.id, step: step.step })}
          >
            Cancel
          </Button>
        )}
      </div>
      {step.error && (
        <p className="ml-7 mt-1 text-xs text-destructive" data-testid={`job-step-error-${step.step}`}>
          {step.error}
        </p>
      )}
    </li>
  );
}

/** Step-level progress for the active or last build job (Issue #109).
 * The job state is persisted server-side, so this panel is restored after
 * a browser reload by polling the latest-job endpoint. */
export function BuildJobPanel({ job }: { job: SystemUnderstandingBuildOut }) {
  const cancelJob = useCancelSystemUnderstandingJob();
  const retryJob = useRetrySystemUnderstandingJob();
  const active = job.status === "queued" || job.status === "running";
  const completedSteps = job.steps.filter((s) => s.status === "completed").length;
  const retryableJob =
    TERMINAL_JOB_STATUSES.includes(job.status) &&
    job.steps.some((s) => ["failed", "blocked", "cancelled", "pending"].includes(s.status));

  return (
    <Card data-testid="build-job-panel">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            Build job #{job.id}
            <Badge variant={jobStatusVariant(job.status)} className="text-xs" data-testid="job-status">
              {job.status}
            </Badge>
            {job.is_stuck && (
              <Badge variant="destructive" className="text-xs" data-testid="job-stuck">
                stuck
              </Badge>
            )}
          </CardTitle>
          <div className="flex gap-2">
            {active && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                data-testid="job-cancel"
                disabled={cancelJob.isPending || job.cancel_requested}
                onClick={() => cancelJob.mutate(job.id)}
              >
                {job.cancel_requested ? "Cancelling..." : "Cancel"}
              </Button>
            )}
            {(retryableJob || job.is_stuck) && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                data-testid="job-retry"
                disabled={retryJob.isPending}
                onClick={() => retryJob.mutate({ jobId: job.id })}
              >
                Retry failed steps
              </Button>
            )}
          </div>
        </div>
        <CardDescription>
          {completedSteps}/{job.steps.length} steps completed
          {active && job.current_step && (
            <> — running: {JOB_STEP_LABELS[job.current_step] ?? job.current_step}</>
          )}
          {active && " . This page keeps working normally while the build runs in the background."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {job.is_stuck && (
          <p className="text-xs text-destructive">
            No heartbeat has been received recently; the worker may have died.
            Retry to resume from the last persisted step.
          </p>
        )}
        {job.error && (
          <p className="text-sm text-destructive" data-testid="job-error">
            Last error: {job.error}
          </p>
        )}
        <ul className="space-y-2" data-testid="job-steps">
          {job.steps.map((s) => (
            <JobStepRow key={s.id} job={job} step={s} active={active} />
          ))}
        </ul>
        {job.artifact_counts && (
          <div className="flex flex-wrap gap-4 border-t pt-3 text-xs text-muted-foreground" data-testid="job-artifact-counts">
            <span>Symbols: {job.artifact_counts.symbols}</span>
            <span>Entrypoints: {job.artifact_counts.entrypoints}</span>
            <span>Doc claims: {job.artifact_counts.understanding_graph_claims}</span>
            <span>Hierarchy nodes: {job.artifact_counts.capability_hierarchy_nodes}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
