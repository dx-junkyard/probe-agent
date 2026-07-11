import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useCreateIssueDraft,
  useUpdateIssueDraft,
  useIssueDraft,
  useGitHubIssueStatus,
  useCreateGitHubIssue,
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
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import {
  XCircle, AlertTriangle, HelpCircle, ExternalLink, FileText, Code, Zap, Copy, FilePlus2,
  ArrowDown, ArrowUp, Minus,
} from "lucide-react";
import type {
  SystemUnderstandingGap,
  SystemUnderstandingGapTrend,
  IssueDraft,
  IssueDraftRef,
  IssueDraftStatus,
} from "@/api/types";

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
  const githubStatus = useGitHubIssueStatus();
  const createGithubIssue = useCreateGitHubIssue();
  const [title, setTitle] = useState(draft.title);
  const [body, setBody] = useState(draft.body_markdown);
  const [status, setStatus] = useState<IssueDraftStatus>(draft.status);
  const [externalUrl, setExternalUrl] = useState(draft.external_url ?? "");

  const createOnGithub = () => {
    createGithubIssue.mutate(draft.id, {
      onSuccess: (updated) => {
        setExternalUrl(updated.external_url ?? "");
        setStatus(updated.status);
        toast.success("GitHub issue created");
      },
      onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Could not create GitHub issue"),
    });
  };

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
          {/* Issue #158: show which snapshot/commit the draft was generated from
              and whether that analysis is now stale relative to the latest snapshot. */}
          <div className="rounded-md border px-3 py-2 text-xs text-muted-foreground" data-testid="issue-draft-provenance">
            Generated from{" "}
            {draft.snapshot_id != null ? <>snapshot #{draft.snapshot_id}</> : "an unknown snapshot"}
            {draft.commit_sha ? <> at <code className="font-mono">{draft.commit_sha.slice(0, 8)}</code></> : null}.
            {draft.stale && (
              <span
                className="ml-1 font-medium text-amber-700 dark:text-amber-400"
                data-testid="issue-draft-stale"
              >
                {" "}The repository has a newer snapshot — this draft may be stale. Refresh
                System Understanding and regenerate the draft before creating a new issue.
              </span>
            )}
          </div>
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
            {githubStatus.data?.available ? (
              <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2" data-testid="issue-draft-github-available">
                <p className="text-xs text-muted-foreground">
                  GitHub is configured for{" "}
                  <code className="font-mono">{githubStatus.data.owner}/{githubStatus.data.repo}</code>.
                  Create the issue directly, or paste an existing tracker URL below.
                </p>
                <Button
                  size="sm"
                  className="h-7 text-xs shrink-0"
                  onClick={createOnGithub}
                  disabled={createGithubIssue.isPending}
                  data-testid="issue-draft-create-github"
                >
                  Create GitHub issue
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground" data-testid="issue-draft-github-unavailable">
                Create the issue in your tracker (GitHub, GitLab, Jira, ...), then paste its URL here.
                {githubStatus.data?.reason ? ` (${githubStatus.data.reason})` : ""}
              </p>
            )}
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
            // Issue #176: carry the gap's capability key so Flow Explorer shows
            // a way back to the capability this gap was found under.
            const flowLink = er.entrypoint_type && er.entrypoint_ref
              ? `/flow-explorer?entrypoint_type=${encodeURIComponent(er.entrypoint_type)}&entrypoint_id=${encodeURIComponent(er.entrypoint_ref)}`
                + (gap.capability_key ? `&capability=${encodeURIComponent(gap.capability_key)}` : "")
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

// Issue #203: gap-count trend between the last two settled builds. Fewer
// gaps than before is the positive direction for a docs-code gap count, so a
// decrease is styled distinctly from an increase or unchanged/no-history.
function GapTrendSummary({ gapTrend }: { gapTrend?: SystemUnderstandingGapTrend[] }) {
  if (!gapTrend || gapTrend.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2" data-testid="gap-trend">
      {gapTrend.map((t) => {
        const delta = t.current - t.previous;
        const improved = delta < 0;
        const worsened = delta > 0;
        return (
          <Badge
            key={t.gap_type}
            variant="outline"
            className={cn(
              "text-xs flex items-center gap-1",
              improved && "border-green-600 text-green-700 dark:text-green-400",
              worsened && "border-red-600 text-red-700 dark:text-red-400",
            )}
          >
            {improved ? (
              <ArrowDown className="h-3 w-3" />
            ) : worsened ? (
              <ArrowUp className="h-3 w-3" />
            ) : (
              <Minus className="h-3 w-3" />
            )}
            {t.gap_type} {t.previous} → {t.current}
          </Badge>
        );
      })}
    </div>
  );
}

export function GapWorklist({ gaps, gapSummary, gapTrend, snapshotId, commitSha }: {
  gaps: SystemUnderstandingGap[];
  gapSummary: { gap_type: string; count: number }[];
  gapTrend?: SystemUnderstandingGapTrend[];
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
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground" data-testid="no-gaps-message">
            No significant differences found between documentation and code.
          </p>
          <GapTrendSummary gapTrend={gapTrend} />
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
        <GapTrendSummary gapTrend={gapTrend} />
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
