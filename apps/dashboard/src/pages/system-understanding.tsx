import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  useSystemUnderstanding,
  useBuildSystemUnderstanding,
  useLatestSystemUnderstandingBuild,
  useCancelSystemUnderstandingJob,
  useRetrySystemUnderstandingJob,
  useCancelSystemUnderstandingStep,
  useSystemDiagnostics,
  useCreateIssueDraft,
  useUpdateIssueDraft,
  useIssueDraft,
  sysKey,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { DiagnosticCheckCard, EnvFixDialog } from "@/components/diagnostics-badge";
import {
  useDiagnosticActivate, useDiagnosticHighlight, DiagnosticFixCallout,
} from "@/components/diagnostic-fix";
import { toast } from "sonner";
import {
  CheckCircle2, XCircle, AlertTriangle, Ban, HelpCircle,
  RefreshCw, ArrowRight, ExternalLink, FileText, Code, Zap,
  Boxes, Target, Stethoscope, Copy, FilePlus2,
} from "lucide-react";
import type {
  SystemDiagnosticCheck,
  SystemUnderstandingPipelineStep,
  SystemUnderstandingNextAction,
  SystemUnderstandingGap,
  SystemUnderstandingOut,
  SystemUnderstandingBuildOut,
  SystemUnderstandingBuildStep,
  IssueDraft,
  IssueDraftRef,
  IssueDraftStatus,
} from "@/api/types";

const STEP_LABELS: Record<string, string> = {
  repository_configured: "Repository configured",
  snapshot_ready: "Snapshot ready",
  documentation_indexed: "Documentation indexed",
  documentation_claims_scanned: "Documentation claims scanned",
  symbols_indexed: "Code symbols indexed",
  entrypoints_discovered: "Entrypoints discovered",
  docs_code_reconciled: "Docs-code reconciled",
  capability_hierarchy_ready: "Capability hierarchy ready",
};

// Build job step names (Issue #109) differ from the artifact pipeline above.
const JOB_STEP_LABELS: Record<string, string> = {
  symbol_index: "Symbol index",
  entrypoint_index: "Entrypoint discovery",
  documentation_index: "Documentation index",
  claim_scan: "Documentation claim scan (LLM)",
  understanding_graph: "Understanding graph",
  docs_code_reconcile: "Docs-code reconcile",
  capability_hierarchy: "Capability hierarchy",
};

const TERMINAL_JOB_STATUSES = ["completed", "partial", "failed", "cancelled"];

const STEP_LINKS: Record<string, string> = {
  repository_configured: "/repository",
  snapshot_ready: "/repository",
  symbols_indexed: "/repository",
  entrypoints_discovered: "/flow-explorer",
  capability_hierarchy_ready: "/capability-map",
  docs_code_reconciled: "/system-understanding",
};

function StatusIcon({ status }: { status: string }) {
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

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "complete": return "default";
    case "warning": return "secondary";
    case "failed": return "destructive";
    case "blocked": return "outline";
    default: return "outline";
  }
}

function PipelineChecklist({ steps, checksByStep }: {
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
function BuildJobPanel({ job }: { job: SystemUnderstandingBuildOut }) {
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

function NextActionsList({ actions }: { actions: SystemUnderstandingNextAction[] }) {
  return (
    <ul className="space-y-2" data-testid="next-actions">
      {actions.map((a, i) => (
        <li key={i} className="flex items-start gap-3 text-sm">
          <ArrowRight className="h-4 w-4 text-primary mt-0.5 shrink-0" />
          <div className="flex-1">
            <span className="font-medium">
              {a.link ? (
                <Link to={a.link} className="hover:underline">{a.action}</Link>
              ) : (
                a.action
              )}
            </span>
            <p className="text-muted-foreground text-xs mt-0.5">{a.reason}</p>
          </div>
          {a.link && (
            <Link to={a.link}>
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground" />
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

function SeverityIcon({ severity }: { severity: string }) {
  switch (severity) {
    case "warning": return <AlertTriangle className="h-4 w-4 text-yellow-600" />;
    case "error": return <XCircle className="h-4 w-4 text-red-600" />;
    default: return <HelpCircle className="h-4 w-4 text-blue-500" />;
  }
}

const CREATE_ISSUE_ACTION = "Create implementation issue";

const ISSUE_DRAFT_STATUS_LABELS: Record<IssueDraftStatus, string> = {
  draft: "Draft",
  copied: "Copied",
  external_created: "Issue created",
  closed: "Closed",
  rejected: "Rejected",
};

function copyMarkdown(text: string) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toast.success("Markdown copied to clipboard"),
      () => toast.error("Could not copy Markdown"),
    );
  } else {
    toast.error("Clipboard is not available");
  }
}

// Issue #107: draft editor. Loads the full draft (body_markdown) by id, lets the
// developer edit title/body, copy the Markdown into any tracker, register the
// external issue URL they created, and set the draft status. probe-agent never
// creates the external issue itself.
function IssueDraftDialog({ draftId, onClose }: { draftId: number | null; onClose: () => void }) {
  const { data: draft, isLoading } = useIssueDraft(draftId);
  return (
    <Dialog open={draftId != null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogHeader>
        <DialogTitle>Issue draft</DialogTitle>
      </DialogHeader>
      {isLoading || !draft ? (
        <div className="space-y-2" data-testid="issue-draft-loading">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        // Remount on id change so the editor re-initializes its local form
        // state from the freshly loaded draft without a state-sync effect.
        <IssueDraftEditor key={draft.id} draft={draft} />
      )}
    </Dialog>
  );
}

function IssueDraftEditor({ draft }: { draft: IssueDraft }) {
  const update = useUpdateIssueDraft();
  const [title, setTitle] = useState(draft.title);
  const [body, setBody] = useState(draft.body_markdown);
  const [status, setStatus] = useState<IssueDraftStatus>(draft.status);
  const [externalUrl, setExternalUrl] = useState(draft.external_url ?? "");

  const saveContent = () => {
    update.mutate(
      { id: draft.id, body: { title, body_markdown: body, status } },
      { onSuccess: () => toast.success("Draft saved") },
    );
  };

  const registerUrl = () => {
    update.mutate(
      {
        id: draft.id,
        body: {
          external_url: externalUrl,
          status: externalUrl.trim() ? "external_created" : status,
        },
      },
      {
        onSuccess: () => toast.success(externalUrl.trim() ? "External URL registered" : "External URL cleared"),
        onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Could not register URL"),
      },
    );
  };

  return (
        <div className="space-y-4" data-testid="issue-draft-dialog">
          <div className="space-y-1.5">
            <Label htmlFor="issue-draft-title">Title</Label>
            <Input
              id="issue-draft-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              data-testid="issue-draft-title"
            />
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="issue-draft-body">Markdown body</Label>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                onClick={() => copyMarkdown(body)}
                data-testid="issue-draft-copy"
              >
                <Copy className="h-3 w-3 mr-1" /> Copy Markdown
              </Button>
            </div>
            <Textarea
              id="issue-draft-body"
              value={body}
              rows={12}
              onChange={(e) => setBody(e.target.value)}
              className="font-mono text-xs"
              data-testid="issue-draft-body"
            />
          </div>
          <div className="flex items-end gap-2">
            <div className="space-y-1.5">
              <Label htmlFor="issue-draft-status">Status</Label>
              <Select
                id="issue-draft-status"
                value={status}
                onChange={(e) => setStatus(e.target.value as IssueDraftStatus)}
                data-testid="issue-draft-status"
                className="w-40"
              >
                {(Object.keys(ISSUE_DRAFT_STATUS_LABELS) as IssueDraftStatus[]).map((s) => (
                  <option key={s} value={s}>{ISSUE_DRAFT_STATUS_LABELS[s]}</option>
                ))}
              </Select>
            </div>
            <Button onClick={saveContent} disabled={update.isPending} data-testid="issue-draft-save">
              Save
            </Button>
          </div>
          <div className="space-y-1.5 border-t pt-4">
            <Label htmlFor="issue-draft-url">External issue URL</Label>
            <p className="text-xs text-muted-foreground">
              Create the issue in your tracker (GitHub, GitLab, Jira, ...), then paste its URL here.
              probe-agent does not create or sync the issue.
            </p>
            <div className="flex gap-2">
              <Input
                id="issue-draft-url"
                value={externalUrl}
                placeholder="https://github.com/org/repo/issues/123"
                onChange={(e) => setExternalUrl(e.target.value)}
                data-testid="issue-draft-url"
              />
              <Button
                variant="outline"
                onClick={registerUrl}
                disabled={update.isPending}
                data-testid="issue-draft-register-url"
              >
                Register
              </Button>
            </div>
            {draft.external_url && (
              <a
                href={draft.external_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                data-testid="issue-draft-external-link"
              >
                <ExternalLink className="h-3 w-3" /> {draft.external_url}
              </a>
            )}
          </div>
        </div>
  );
}

function IssueDraftBadges({ drafts, onOpen }: { drafts: IssueDraftRef[]; onOpen: (id: number) => void }) {
  if (drafts.length === 0) return null;
  return (
    <div className="pl-6 flex flex-wrap gap-2" data-testid="gap-issue-drafts">
      {drafts.map((d) => {
        const statusLabel = ISSUE_DRAFT_STATUS_LABELS[d.status as IssueDraftStatus] ?? d.status;
        return (
          <div key={d.id} className="flex items-center gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              className="h-7 text-xs"
              onClick={() => onOpen(d.id)}
              data-testid="gap-issue-draft-open"
            >
              <FileText className="h-3 w-3 mr-1" /> Issue draft · {statusLabel}
            </Button>
            {d.external_url && (
              <a
                href={d.external_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                data-testid="gap-issue-draft-url"
              >
                <ExternalLink className="h-3 w-3" /> issue
              </a>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GapCard({ gap, snapshotId, commitSha }: {
  gap: SystemUnderstandingGap;
  snapshotId: number | null;
  commitSha: string | null;
}) {
  const createDraft = useCreateIssueDraft();
  const [openDraftId, setOpenDraftId] = useState<number | null>(null);
  const drafts = gap.issue_drafts ?? [];

  const handleCreateIssue = () => {
    if (drafts.length > 0) {
      setOpenDraftId(drafts[0].id);
      return;
    }
    createDraft.mutate(
      { gap, snapshot_id: snapshotId, commit_sha: commitSha },
      {
        onSuccess: (draft) => {
          setOpenDraftId(draft.id);
          toast.success("Issue draft generated");
        },
        onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Could not generate draft"),
      },
    );
  };

  return (
    <div className="rounded-lg border p-4 space-y-3" data-testid="gap-card">
      <div className="flex items-start gap-2">
        <SeverityIcon severity={gap.severity} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">{gap.title ?? gap.node_name ?? "Unknown gap"}</p>
          <div className="flex flex-wrap items-center gap-1.5 mt-1">
            <Badge variant="outline" className="text-xs">{gap.gap_type}</Badge>
            <Badge variant={gap.severity === "warning" ? "secondary" : "outline"} className="text-xs">{gap.severity}</Badge>
            {gap.capability_key && (
              <Badge variant="secondary" className="text-xs">
                <Link to={`/capability-map?capability=${encodeURIComponent(gap.capability_key)}`} className="hover:underline">{gap.capability_key}</Link>
              </Badge>
            )}
          </div>
        </div>
      </div>

      {gap.notes && (
        <p className="text-xs text-muted-foreground pl-6">{gap.notes}</p>
      )}

      {(gap.doc_refs.length > 0 || gap.symbol_refs.length > 0 || gap.entrypoint_refs.length > 0) && (
        <div className="pl-6 space-y-1.5 text-xs">
          {gap.doc_refs.map((dr, i) => (
            <div key={`doc-${i}`} className="flex items-center gap-1.5 text-muted-foreground">
              <FileText className="h-3 w-3 shrink-0" />
              <span className="font-mono">
                {dr.path}
                {dr.start_line != null && dr.end_line != null && `:${dr.start_line}-${dr.end_line}`}
              </span>
            </div>
          ))}
          {gap.symbol_refs.map((sr, i) => (
            <div key={`sym-${i}`} className="flex items-center gap-1.5 text-muted-foreground">
              <Code className="h-3 w-3 shrink-0" />
              <span className="font-mono">
                {sr.path && `${sr.path}: `}{sr.qualified_name}
              </span>
            </div>
          ))}
          {gap.entrypoint_refs.map((er, i) => {
            const label = [er.entrypoint_type, er.entrypoint_ref].filter(Boolean).join(": ") || "Unknown entrypoint";
            const flowLink = er.entrypoint_type && er.entrypoint_ref
              ? `/flow-explorer?entrypoint_type=${encodeURIComponent(er.entrypoint_type)}&entrypoint_id=${encodeURIComponent(er.entrypoint_ref)}`
              : null;

            return (
              <div key={`ep-${i}`} className="flex items-center gap-1.5 text-muted-foreground">
                <Zap className="h-3 w-3 shrink-0" />
                {flowLink ? (
                  <Link
                    to={flowLink}
                    className="font-mono text-primary hover:underline"
                    data-testid="gap-entrypoint-link"
                  >
                    {label}
                  </Link>
                ) : (
                  <span className="font-mono">{label}</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      <IssueDraftBadges drafts={drafts} onOpen={setOpenDraftId} />

      {gap.next_actions.length > 0 && (
        <div className="pl-6 flex flex-wrap gap-2">
          {gap.next_actions.map((na, i) => {
            if (na.action === CREATE_ISSUE_ACTION) {
              // Issue #107: connect the placeholder next action to the draft flow.
              return (
                <Button
                  key={i}
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  onClick={handleCreateIssue}
                  disabled={createDraft.isPending}
                  data-testid="gap-create-issue"
                >
                  <FilePlus2 className="h-3 w-3 mr-1" />
                  {drafts.length > 0 ? "Open issue draft" : na.action}
                </Button>
              );
            }
            return na.link ? (
              <Link key={i} to={na.link}>
                <Button variant="outline" size="sm" className="h-7 text-xs">
                  {na.action}
                </Button>
              </Link>
            ) : (
              <Button key={i} variant="outline" size="sm" className="h-7 text-xs" disabled>
                {na.action}
              </Button>
            );
          })}
        </div>
      )}

      {openDraftId != null && (
        <IssueDraftDialog draftId={openDraftId} onClose={() => setOpenDraftId(null)} />
      )}
    </div>
  );
}

function GapWorklist({ gaps, gapSummary, snapshotId, commitSha }: {
  gaps: SystemUnderstandingGap[];
  gapSummary: { gap_type: string; count: number }[];
  snapshotId: number | null;
  commitSha: string | null;
}) {
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [capabilityFilter, setCapabilityFilter] = useState<string | null>(null);

  if (gaps.length === 0 && gapSummary.length === 0) {
    return (
      <Card data-testid="gap-worklist">
        <CardHeader>
          <CardTitle className="text-base">Docs-Code Gaps</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground" data-testid="no-gaps-message">
            No significant differences found between documentation and code.
          </p>
        </CardContent>
      </Card>
    );
  }

  const allTypes = Array.from(new Set(gaps.map((g) => g.gap_type ?? "unknown")));
  const allCapabilities = Array.from(new Set(gaps.map((g) => g.capability_key).filter(Boolean))) as string[];
  const filtered = gaps
    .filter((g) => !typeFilter || (g.gap_type ?? "unknown") === typeFilter)
    .filter((g) => !capabilityFilter || g.capability_key === capabilityFilter);

  const severityCounts = gaps.reduce((acc, g) => {
    acc[g.severity] = (acc[g.severity] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  return (
    <Card data-testid="gap-worklist">
      <CardHeader>
        <CardTitle className="text-base">Docs-Code Gap Worklist</CardTitle>
        <CardDescription>
          {gaps.length} gap{gaps.length !== 1 ? "s" : ""} found
          {Object.entries(severityCounts).map(([sev, cnt]) => (
            <span key={sev}> / {cnt} {sev}</span>
          ))}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Summary chips */}
        <div className="flex flex-wrap gap-2" data-testid="gap-summary">
          <Button
            variant={typeFilter === null ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setTypeFilter(null)}
          >
            All ({gaps.length})
          </Button>
          {allTypes.map((t) => {
            const count = gaps.filter((g) => (g.gap_type ?? "unknown") === t).length;
            return (
              <Button
                key={t}
                variant={typeFilter === t ? "default" : "outline"}
                size="sm"
                className="h-7 text-xs"
                onClick={() => setTypeFilter(typeFilter === t ? null : t)}
              >
                {t} ({count})
              </Button>
            );
          })}
        </div>

        {/* Capability filter */}
        {allCapabilities.length > 0 && (
          <div className="flex flex-wrap gap-2" data-testid="gap-capability-filter">
            <Button
              variant={capabilityFilter === null ? "default" : "outline"}
              size="sm"
              className="h-7 text-xs"
              onClick={() => setCapabilityFilter(null)}
            >
              All capabilities
            </Button>
            {allCapabilities.map((cap) => {
              const count = gaps.filter((g) => g.capability_key === cap).length;
              return (
                <Button
                  key={cap}
                  variant={capabilityFilter === cap ? "default" : "outline"}
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => setCapabilityFilter(capabilityFilter === cap ? null : cap)}
                >
                  {cap} ({count})
                </Button>
              );
            })}
          </div>
        )}

        {/* Gap cards */}
        <div className="space-y-3" data-testid="gap-cards">
          {filtered.map((gap, i) => (
            <GapCard key={i} gap={gap} snapshotId={snapshotId} commitSha={commitSha} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="py-10 text-center">
        <h3 className="text-lg font-semibold mb-4">Get started with System Understanding</h3>
        <ol className="text-sm text-muted-foreground space-y-2 text-left max-w-md mx-auto list-decimal list-inside">
          <li><Link to="/repository" className="hover:underline text-primary">Configure your repository</Link></li>
          <li>Create a snapshot from a commit</li>
          <li>Index README/docs and source code</li>
          <li>Build system understanding</li>
          <li><Link to="/capability-map" className="hover:underline text-primary">Explore capabilities and API boundaries</Link></li>
        </ol>
      </CardContent>
    </Card>
  );
}

function EntryCards() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Link to="/capability-map" className="block group">
        <Card className="h-full transition-colors group-hover:border-primary/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Boxes className="h-4 w-4" />
              Start from Capability
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Browse capabilities discovered from source metadata and code structure.
            Drill into elements, see related APIs, and identify probe candidates.
          </CardContent>
        </Card>
      </Link>
      <Link to="/feature-map" className="block group">
        <Card className="h-full transition-colors group-hover:border-primary/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Target className="h-4 w-4" />
              Start from Feature
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Explore features extracted from documentation, map them to code symbols
            via Feature-to-Code Links, and trace their capability connections.
          </CardContent>
        </Card>
      </Link>
    </div>
  );
}

function DataView({ data, checksByStep }: {
  data: SystemUnderstandingOut;
  checksByStep: Record<string, SystemDiagnosticCheck[]>;
}) {
  const pipeline = data.pipeline ?? [];
  const allMissing = pipeline.every((s) => s.status === "missing");

  return (
    <div className="space-y-6">
      {/* Pipeline Checklist */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pipeline Status</CardTitle>
          <CardDescription>
            Progress through the system understanding pipeline. Steps that are
            missing or blocked show a "Why?" button when a configuration
            diagnostic or a recent run failure explains them.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {allMissing ? <EmptyState /> : <PipelineChecklist steps={pipeline} checksByStep={checksByStep} />}
        </CardContent>
      </Card>

      {!allMissing && <EntryCards />}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* System Purpose */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">System Purpose</CardTitle>
          </CardHeader>
          <CardContent>
            {data.purpose ? (
              <div>
                <p className="font-medium">{data.purpose.name}</p>
                {data.purpose.summary && (
                  <p className="text-sm text-muted-foreground mt-1">{data.purpose.summary}</p>
                )}
                {data.purpose.provenance_kind && (
                  <Badge variant="outline" className="mt-2 text-xs">{data.purpose.provenance_kind}</Badge>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No system purpose defined yet.</p>
            )}
          </CardContent>
        </Card>

        {/* Metadata Coverage */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Metadata Coverage</CardTitle>
          </CardHeader>
          <CardContent>
            {data.metadata_coverage ? (
              <div className="grid grid-cols-2 gap-4 text-sm" data-testid="metadata-coverage">
                <div>
                  <p className="text-muted-foreground">Symbols</p>
                  <p className="text-lg font-semibold">{data.metadata_coverage.symbol_count}</p>
                  <p className="text-xs text-muted-foreground">
                    {data.metadata_coverage.symbols_with_source_metadata} with metadata
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Entrypoints</p>
                  <p className="text-lg font-semibold">{data.metadata_coverage.entrypoint_count}</p>
                  <p className="text-xs text-muted-foreground">
                    {data.metadata_coverage.entrypoints_with_capability_link} with capability link
                  </p>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Run a build to see coverage.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Core Capabilities */}
      {data.capabilities.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Core Capabilities</CardTitle>
            <CardDescription>
              <Link to="/capability-map" className="hover:underline text-primary">
                View full Capability Map
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {data.capabilities.map((c, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <Badge variant="outline" className="mt-0.5 shrink-0">{c.provenance_kind ?? "unknown"}</Badge>
                  <div>
                    <Link to={`/capability-map?capability=${encodeURIComponent(c.name)}`} className="font-medium hover:underline">{c.name}</Link>
                    {c.summary && <p className="text-muted-foreground text-xs">{c.summary}</p>}
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Key Entrypoints */}
      {data.entrypoints.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Key API / Entrypoints</CardTitle>
            <CardDescription>
              <Link to="/flow-explorer" className="hover:underline text-primary">
                View in Flow Explorer
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 font-medium text-muted-foreground">Type</th>
                    <th className="pb-2 font-medium text-muted-foreground">ID</th>
                    <th className="pb-2 font-medium text-muted-foreground">Category</th>
                    <th className="pb-2 font-medium text-muted-foreground">Label</th>
                  </tr>
                </thead>
                <tbody>
                  {data.entrypoints.slice(0, 20).map((ep, i) => (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-1.5">
                        <Badge variant="outline" className="text-xs">{ep.entrypoint_type}</Badge>
                      </td>
                      <td className="py-1.5 font-mono text-xs">
                        <Link
                          to={`/flow-explorer?entrypoint_type=${encodeURIComponent(ep.entrypoint_type)}&entrypoint_id=${encodeURIComponent(ep.entrypoint_id)}`}
                          className="text-primary hover:underline"
                          data-testid="entrypoint-flow-link"
                        >
                          {ep.entrypoint_id}
                        </Link>
                      </td>
                      <td className="py-1.5">{ep.category ?? "—"}</td>
                      <td className="py-1.5">{ep.label ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.entrypoints.length > 20 && (
                <p className="text-xs text-muted-foreground mt-2">
                  Showing 20 of {data.entrypoints.length}.{" "}
                  <Link to="/flow-explorer" className="hover:underline text-primary">View all</Link>
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Major Symbols */}
      {data.major_symbols.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Major Functions / Source Symbols</CardTitle>
            <CardDescription>
              <Link to="/repository" className="hover:underline text-primary">
                View in Repository
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 font-medium text-muted-foreground">Path</th>
                    <th className="pb-2 font-medium text-muted-foreground">Name</th>
                    <th className="pb-2 font-medium text-muted-foreground">Kind</th>
                    <th className="pb-2 font-medium text-muted-foreground">Route</th>
                  </tr>
                </thead>
                <tbody>
                  {data.major_symbols.slice(0, 20).map((sym, i) => (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-1.5 font-mono text-xs">{sym.path}</td>
                      <td className="py-1.5 font-mono text-xs">{sym.qualified_name}</td>
                      <td className="py-1.5">{sym.kind ?? "—"}</td>
                      <td className="py-1.5 font-mono text-xs">
                        {sym.route_method && sym.route_path ? (
                          <Link
                            to={`/flow-explorer?entrypoint_type=api&entrypoint_id=${encodeURIComponent(`${sym.route_method} ${sym.route_path}`)}`}
                            className="text-primary hover:underline"
                            data-testid="symbol-flow-link"
                          >
                            {sym.route_method} {sym.route_path}
                          </Link>
                        ) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.major_symbols.length > 20 && (
                <p className="text-xs text-muted-foreground mt-2">
                  Showing 20 of {data.major_symbols.length}.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Docs-Code Gap Worklist */}
      <GapWorklist gaps={data.gaps} gapSummary={data.gap_summary} snapshotId={data.snapshot_id} commitSha={data.commit_sha} />

      {/* Next Actions */}
      {data.next_actions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Next Actions</CardTitle>
            <CardDescription>Steps to improve system understanding</CardDescription>
          </CardHeader>
          <CardContent>
            <NextActionsList actions={data.next_actions} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function SystemUnderstandingPage() {
  const { data, isLoading, error } = useSystemUnderstanding();
  const { data: diagnostics } = useSystemDiagnostics();
  const build = useBuildSystemUnderstanding();
  const { data: latestBuild } = useLatestSystemUnderstandingBuild();
  const qc = useQueryClient();
  const settledBuildId = useRef<number | null>(null);

  const buildRunning = latestBuild?.status === "queued" || latestBuild?.status === "running";
  const buildHighlight = useDiagnosticHighlight<HTMLButtonElement>("build");

  // Refresh the aggregated view and diagnostics once a build job settles.
  useEffect(() => {
    if (!latestBuild) return;
    if (!TERMINAL_JOB_STATUSES.includes(latestBuild.status)) return;
    if (settledBuildId.current === latestBuild.id) return;
    settledBuildId.current = latestBuild.id;
    qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
    qc.invalidateQueries({ queryKey: sysKey("system-diagnostics") });
  }, [latestBuild, qc]);

  const checksByStep = useMemo(() => {
    const map: Record<string, SystemDiagnosticCheck[]> = {};
    for (const check of diagnostics?.checks ?? []) {
      if (check.severity === "ok") continue;
      for (const step of check.related_pipeline_steps) {
        (map[step] ??= []).push(check);
      }
    }
    return map;
  }, [diagnostics]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">System Understanding</h1>
          <p className="text-muted-foreground mt-1">
            {data?.commit_sha
              ? `Snapshot #${data.snapshot_id} — ${data.commit_sha.slice(0, 8)}`
              : "Unified view of what is known about this system"}
          </p>
        </div>
        <Button
          {...buildHighlight}
          onClick={() => build.mutate()}
          disabled={build.isPending || buildRunning}
          variant="default"
          data-testid="build-button"
        >
          {build.isPending || buildRunning ? (
            <>
              <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
              Building...
            </>
          ) : (
            <>
              <RefreshCw className="h-4 w-4 mr-2" />
              Build / Refresh
            </>
          )}
        </Button>
      </div>

      <DiagnosticFixCallout anchor="build" />

      {latestBuild && (buildRunning || latestBuild.is_stuck ||
        latestBuild.status === "failed" || latestBuild.status === "partial" ||
        latestBuild.status === "cancelled") && (
        <div data-testid={buildRunning ? "build-progress" : "build-settled"}>
          <BuildJobPanel job={latestBuild} />
        </div>
      )}

      {(latestBuild?.status === "failed" || latestBuild?.status === "partial") && (
        <Card data-testid="build-failed">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Last build {latestBuild.status === "partial" ? "partially failed" : "failed"}
              {latestBuild.error ? `: ${latestBuild.error}` : "."}
            </p>
          </CardContent>
        </Card>
      )}

      {error && (
        <Card>
          <CardContent className="py-4">
            <p className="text-sm text-destructive">Failed to load system understanding: {String(error)}</p>
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-32 w-full" />)}
        </div>
      ) : data ? (
        <DataView data={data} checksByStep={checksByStep} />
      ) : null}
    </div>
  );
}
