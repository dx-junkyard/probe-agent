import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useGithubAppStatus, useGithubConnections, useCreateGithubConnection,
  useVerifyGithubConnection, useSyncGithubConnection, useDeleteGithubConnection,
  useInstallationRepositories, usePublishJobs, useCreatePublishJob,
  useApprovePublishJob, useCancelPublishJob, useRetryPublishJob, useProbePatches, useUsers,
  useSystemGithubInstallations, useGithubInstallations, useRegisterGithubInstallation,
  useDisableGithubInstallation, useAssignGithubInstallation, useUnassignGithubInstallation,
  usePublishJobEvents,
} from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import {
  GitMerge, RefreshCw, CheckCircle2, XCircle, Trash2, Plus, ExternalLink,
  ShieldCheck, GitPullRequest, AlertTriangle, RotateCcw, History,
} from "lucide-react";
import type { GithubConnectionOut, GithubInstallationOut, PublishJobOut, ProbePatchOut } from "@/api/types";

function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "—";
}

function connectionStatusVariant(status: GithubConnectionOut["status"]) {
  if (status === "connected") return "success" as const;
  if (status === "error") return "destructive" as const;
  if (status === "disconnected") return "secondary" as const;
  return "warning" as const;
}

const IN_PROGRESS_STATUSES = new Set<PublishJobOut["status"]>([
  "pending", "authenticating", "fetching", "checking_out", "applying_patch",
  "validating", "committing", "pushing", "creating_pr", "reconciling",
]);

function publishJobStatusVariant(status: PublishJobOut["status"]) {
  if (status === "completed") return "success" as const;
  if (status === "failed" || status === "cancelled" || status === "manual_intervention_required") {
    return "destructive" as const;
  }
  if (status === "awaiting_approval" || status === "retryable_failed") return "warning" as const;
  if (IN_PROGRESS_STATUSES.has(status)) return "secondary" as const;
  return "outline" as const;
}

/** Approximate readiness check for the create-publish-job picker: the latest
 * (last, since /repository/probe-patches returns validation_runs ordered by
 * id ascending) baseline and probed validation runs both succeeded. The
 * server re-checks this authoritatively at job creation and again right
 * before commit/push (Principle 8's staleness re-check) -- this is only a
 * UI convenience filter, not the source of truth. */
function isPatchValidationGreen(patch: ProbePatchOut): boolean {
  const latest = (variant: string) => {
    const runs = patch.validation_runs.filter(r => r.variant === variant);
    return runs.length > 0 ? runs[runs.length - 1] : null;
  };
  const baseline = latest("baseline");
  const probed = latest("probed");
  return !!baseline?.overall_success && !!probed?.overall_success;
}

export default function GithubPage() {
  const [searchParams] = useSearchParams();
  // Issue #259: forward link from the Probe Planner apply-success next-action
  // (`/github?patch=<id>`) -- land directly on the Publish Jobs tab so the
  // preselected patch (validated in PublishJobsPanel below) is visible
  // without an extra click, following the same cross-page context-passing
  // precedent as `?plan=` / `?capability=` elsewhere.
  const patchIdParam = searchParams.get("patch");
  const patchIdFromUrl = patchIdParam && Number.isInteger(Number(patchIdParam)) ? Number(patchIdParam) : null;
  const [tab, setTab] = useState(() => (patchIdFromUrl !== null ? "publish-jobs" : "connections"));
  const { data: appStatus, isLoading: appStatusLoading } = useGithubAppStatus();
  const { isAdmin, systemId } = useAuth();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
        <GitMerge className="h-6 w-6" /> GitHub
      </h1>

      <AppStatusCard status={appStatus} isLoading={appStatusLoading} />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="connections">Connections</TabsTrigger>
          <TabsTrigger value="publish-jobs">Publish Jobs</TabsTrigger>
          {isAdmin && <TabsTrigger value="installations">Installations</TabsTrigger>}
        </TabsList>

        <TabsContent value="connections">
          <ConnectionsPanel appConfigured={!!appStatus?.configured} />
        </TabsContent>

        <TabsContent value="publish-jobs">
          <PublishJobsPanel prefillPatchId={patchIdFromUrl} />
        </TabsContent>

        {isAdmin && <TabsContent value="installations">
          <InstallationsPanel systemId={systemId} />
        </TabsContent>}
      </Tabs>
    </div>
  );
}

function InstallationsPanel({ systemId }: { systemId: number | null }) {
  const { data: installations, isLoading } = useGithubInstallations();
  const register = useRegisterGithubInstallation();
  const disable = useDisableGithubInstallation();
  const assign = useAssignGithubInstallation();
  const unassign = useUnassignGithubInstallation();
  const [installationId, setInstallationId] = useState("");

  const add = async () => {
    const id = Number(installationId);
    if (!id) return;
    try {
      await register.mutateAsync(id);
      setInstallationId("");
      toast.success("Installationを登録・検証しました");
    } catch (e) { toast.error(String(e)); }
  };

  return <Card>
    <CardHeader>
      <CardTitle className="text-base">許可されたInstallation</CardTitle>
      <CardDescription>
        設定済みのGitHub Organizationのみ登録できます。Connectionを作成する前に、有効なInstallationをこのSystemに割り当ててください。
        セットアップ手順の詳細は
        <a
          href="https://github.com/dx-junkyard/probe-agent/blob/main/docs/github-app-deployment.md"
          target="_blank" rel="noreferrer"
          className="mx-1 inline-flex items-center gap-1 text-primary hover:underline"
        >
          docs/github-app-deployment.md <ExternalLink className="h-3 w-3" />
        </a>
        を参照してください。
      </CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      <div className="flex gap-2">
        <Input value={installationId} onChange={e => setInstallationId(e.target.value)} inputMode="numeric" placeholder="GitHub Installation ID" />
        <Button onClick={add} disabled={register.isPending || !installationId.trim()}>登録</Button>
      </div>
      {isLoading ? <Skeleton className="h-20 w-full" /> : !installations?.length ? (
        <p className="text-sm text-muted-foreground">登録されているInstallationはありません。</p>
      ) : <div className="space-y-2">
        {installations.map((installation: GithubInstallationOut) => {
          const assigned = systemId !== null && installation.assigned_system_ids.includes(systemId);
          return <div key={installation.installation_id} className="rounded-lg border p-3 flex items-center justify-between gap-3">
            <div className="text-sm">
              <code>#{installation.installation_id}</code> · {installation.github_account_login}
              <Badge className="ml-2" variant={installation.status === "active" ? "success" : "destructive"}>{installation.status}</Badge>
              <div className="text-xs text-muted-foreground mt-1">割り当て済みSystem: {installation.assigned_system_ids.join(", ") || "なし"}</div>
            </div>
            <div className="flex gap-2">
              {installation.status === "active" && systemId !== null && !assigned && <Button size="sm" variant="outline" onClick={() => assign.mutateAsync({ installationId: installation.installation_id, systemId }).catch(e => toast.error(String(e)))}>このSystemに割り当て</Button>}
              {systemId !== null && assigned && (
                <Button
                  size="sm" variant="outline"
                  data-testid={`installation-${installation.installation_id}-unassign`}
                  onClick={() => unassign.mutateAsync({ installationId: installation.installation_id, systemId })
                    .then(() => toast.success("このSystemの割り当てを解除しました"))
                    .catch(e => toast.error(String(e)))}
                  disabled={unassign.isPending}
                >
                  このSystemの割り当てを解除
                </Button>
              )}
              {installation.status === "active" && <Button size="sm" variant="destructive" onClick={() => disable.mutateAsync(installation.installation_id).catch(e => toast.error(String(e)))}>無効化</Button>}
            </div>
          </div>;
        })}
      </div>}
    </CardContent>
  </Card>;
}

function AppStatusCard({ status, isLoading }: {
  status: { configured: boolean; app_id: string | null; api_base_url: string; web_base_url: string } | undefined;
  isLoading: boolean;
}) {
  if (isLoading) return <Card><CardContent className="py-6"><Skeleton className="h-16 w-full" /></CardContent></Card>;
  if (!status) return null;

  return (
    <Card data-testid="github-app-status">
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div>
          <CardTitle className="text-base">GitHub App</CardTitle>
          <CardDescription>
            短命のInstallation Tokenがpublishワークフローのcommit/push/PR作成を仲介します。
            tokenはここに永続化・表示されることはありません。
          </CardDescription>
        </div>
        <Badge variant={status.configured ? "success" : "destructive"} data-testid="github-app-configured-badge">
          {status.configured ? "設定済み" : "未設定"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {status.configured ? (
          <div className="text-muted-foreground">
            App ID <code className="font-mono">{status.app_id}</code> · {status.api_base_url}
          </div>
        ) : (
          <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
            Control Server上で <code>GITHUB_APP_ID</code> と <code>GITHUB_APP_PRIVATE_KEY_PATH</code>
            （既存のprivate keyファイルを指す）を設定し、再起動してください。任意:
            <code className="ml-1">GITHUB_API_BASE_URL</code> / <code>GITHUB_WEB_BASE_URL</code>
            （GitHub Enterprise向け）。これらが設定されるまでConnectionとpublish jobは利用できません。
            手順の詳細は<code className="mx-1">docs/github-app-deployment.md</code>を参照してください。
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Connections ──────────────────────────────────────────────────────

function ConnectionsPanel({ appConfigured }: { appConfigured: boolean }) {
  const { data: connections, isLoading } = useGithubConnections();
  const verify = useVerifyGithubConnection();
  const sync = useSyncGithubConnection();
  const disconnect = useDeleteGithubConnection();
  const [showCreate, setShowCreate] = useState(false);
  const [disconnectTarget, setDisconnectTarget] = useState<GithubConnectionOut | null>(null);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div>
          <CardTitle className="text-base">Connections</CardTitle>
          <CardDescription>このSystemが承認済みpatchをpublishできるremote repository。</CardDescription>
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)} disabled={!appConfigured} data-testid="new-connection-button">
          <Plus className="h-4 w-4 mr-1" /> 新規Connection
        </Button>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{[1, 2].map(i => <Skeleton key={i} className="h-20 w-full" />)}</div>
        ) : !connections?.length ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            GitHub connectionがまだありません。publishワークフローを有効にするには作成してください。
          </p>
        ) : (
          <div className="space-y-3" data-testid="connections-list">
            {connections.map(c => (
              <div key={c.id} className="rounded-lg border p-4 space-y-2" data-testid={`connection-${c.id}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm">{c.owner}/{c.repo}</span>
                      <Badge variant={connectionStatusVariant(c.status)}>{c.status}</Badge>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      default branch: <code>{c.default_branch ?? "—"}</code> · installation #{c.installation_id}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      最終sync: {c.last_synced_commit_sha ? (
                        <code className="font-mono">{shortSha(c.last_synced_commit_sha)}</code>
                      ) : "未実行"} {c.last_synced_at ? `(${formatTimestamp(c.last_synced_at)})` : ""}
                    </div>
                    {c.last_error && (
                      <div className="flex items-start gap-1.5 text-xs text-destructive">
                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                        <span data-testid={`connection-${c.id}-error`}>{c.last_error}</span>
                      </div>
                    )}
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <Button
                      size="sm" variant="outline"
                      onClick={() => verify.mutateAsync(c.id).then(r => {
                        if (r.status === "connected") toast.success("Connectionを検証しました");
                        else toast.error(r.last_error ?? "検証に失敗しました");
                      }).catch(e => toast.error(String(e)))}
                      disabled={verify.isPending || c.status === "disconnected"}
                    >
                      <ShieldCheck className="h-3.5 w-3.5 mr-1" /> 検証
                    </Button>
                    <Button
                      size="sm" variant="outline"
                      onClick={() => sync.mutateAsync(c.id).then(r => {
                        if (r.last_synced_commit_sha) toast.success("Mirrorをsyncしました");
                        else toast.error(r.last_error ?? "syncに失敗しました");
                      }).catch(e => toast.error(String(e)))}
                      disabled={sync.isPending || c.status !== "connected"}
                    >
                      <RefreshCw className="h-3.5 w-3.5 mr-1" /> Sync
                    </Button>
                    {c.status !== "disconnected" && (
                      <Button
                        size="sm" variant="ghost"
                        onClick={() => setDisconnectTarget(c)}
                        data-testid={`connection-${c.id}-disconnect`}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>

      <CreateConnectionDialog open={showCreate} onOpenChange={setShowCreate} />

      <Dialog open={disconnectTarget !== null} onOpenChange={(open) => { if (!open) setDisconnectTarget(null); }}>
        <DialogHeader><DialogTitle>Repositoryをdisconnect</DialogTitle></DialogHeader>
        {disconnectTarget && (
          <div className="space-y-4">
            <p className="text-sm">
              <code className="font-mono">{disconnectTarget.owner}/{disconnectTarget.repo}</code> をこの
              Systemからdisconnectします（soft delete）。pending中または承認待ちのpublish jobは
              直ちにcancelされます。commit・push・Pull Request作成中のjobは完了するまで
              disconnectをblockします。完了済みjobとそのPull Requestリンクは監査のため保持され、
              再接続するまで新しいpublish jobは作成できません。
            </p>
            <Button
              variant="destructive" className="w-full"
              onClick={() => disconnect.mutateAsync(disconnectTarget.id).then(() => {
                toast.success("Connectionをdisconnectしました");
                setDisconnectTarget(null);
              }).catch(e => toast.error(String(e)))}
              disabled={disconnect.isPending}
            >
              {disconnect.isPending ? "disconnect中..." : "Disconnect"}
            </Button>
          </div>
        )}
      </Dialog>
    </Card>
  );
}

function CreateConnectionDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const createConnection = useCreateGithubConnection();
  const { data: installations, isLoading: installationsLoading } = useSystemGithubInstallations();
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [manualEntry, setManualEntry] = useState(false);
  const { data: repos, isLoading: reposLoading, isError: reposError } = useInstallationRepositories(installationId);

  const reset = () => {
    setInstallationId(null);
    setOwner("");
    setRepo("");
    setManualEntry(false);
  };

  const submit = async () => {
    if (!installationId || !owner.trim() || !repo.trim()) return;
    try {
      await createConnection.mutateAsync({ owner: owner.trim(), repo: repo.trim(), installation_id: installationId });
      toast.success("Connectionを作成しました — 次に検証してください");
      onOpenChange(false);
      reset();
    } catch (e) { toast.error(String(e)); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogHeader><DialogTitle>新規GitHub connection</DialogTitle></DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>割り当て済みInstallation</Label>
          {installationsLoading ? <Skeleton className="h-9 w-full" /> : (
            <Select
              value={installationId?.toString() ?? ""}
              onChange={e => { setInstallationId(Number(e.target.value) || null); setOwner(""); setRepo(""); setManualEntry(false); }}
              data-testid="installation-select"
            >
              <option value="">割り当て済みInstallationを選択...</option>
              {installations?.map(i => <option key={i.installation_id} value={i.installation_id}>
                #{i.installation_id} · {i.github_account_login}
              </option>)}
            </Select>
          )}
          {!installationsLoading && !installations?.length && <p className="text-xs text-muted-foreground">管理者が先にInstallationを登録し、このSystemに割り当てる必要があります。</p>}
        </div>

        {installationId !== null && (
          reposLoading ? (
            <Skeleton className="h-9 w-full" />
          ) : reposError ? (
            <p className="text-xs text-destructive">
              このInstallationのrepository一覧を取得できませんでした。下でowner/repoを手動入力してください。
            </p>
          ) : repos && repos.length > 0 && !manualEntry ? (
            <div className="space-y-2">
              <Label>Repository</Label>
              <Select
                value={owner && repo ? `${owner}/${repo}` : ""}
                onChange={e => { const [o, r] = e.target.value.split("/"); setOwner(o ?? ""); setRepo(r ?? ""); }}
                data-testid="installation-repo-select"
              >
                <option value="">repositoryを選択...</option>
                {repos.map(r => (
                  <option key={`${r.owner}/${r.name}`} value={`${r.owner}/${r.name}`}>
                    {r.owner}/{r.name}{r.private ? " (private)" : ""}
                  </option>
                ))}
              </Select>
              <Button type="button" variant="ghost" size="sm" className="text-xs" onClick={() => setManualEntry(true)}>
                owner/repoを手動入力
              </Button>
            </div>
          ) : null
        )}

        {(manualEntry || installationId === null || (repos && repos.length === 0)) && (
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-2">
              <Label>Owner</Label>
              <Input value={owner} onChange={e => setOwner(e.target.value)} placeholder="acme" data-testid="owner-input" />
            </div>
            <div className="space-y-2">
              <Label>Repository</Label>
              <Input value={repo} onChange={e => setRepo(e.target.value)} placeholder="widgets" data-testid="repo-input" />
            </div>
          </div>
        )}

        <Button
          className="w-full" onClick={submit}
          disabled={createConnection.isPending || installationId === null || !owner.trim() || !repo.trim()}
        >
          {createConnection.isPending ? "作成中..." : "Connectionを作成"}
        </Button>
      </div>
    </Dialog>
  );
}

// ── Publish jobs ─────────────────────────────────────────────────────

function PublishJobsPanel({ prefillPatchId }: { prefillPatchId: number | null }) {
  const { data: connections } = useGithubConnections();
  const { data: patches } = useProbePatches();
  const [connectionFilter, setConnectionFilter] = useState<number | null>(null);
  const { data: jobs, isLoading } = usePublishJobs(connectionFilter);
  // Issue #259: only preselect the patch carried in `?patch=` (and only
  // auto-open the create dialog for it) when it still exists and still
  // passes the same green-validation gate the manual patch picker enforces
  // below -- a stale or invalid id must fall back to the ordinary empty
  // create flow, not a broken preselection.
  const prefillPatch = prefillPatchId !== null
    ? (patches ?? []).find(p => p.id === prefillPatchId && isPatchValidationGreen(p)) ?? null
    : null;
  const [showCreate, setShowCreate] = useState(false);
  const [prefillDismissed, setPrefillDismissed] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);

  const connectionById = useMemo(() => {
    const map = new Map<number, GithubConnectionOut>();
    (connections ?? []).forEach(c => map.set(c.id, c));
    return map;
  }, [connections]);

  const selectedJob = jobs?.find(j => j.id === selectedJobId) ?? null;
  const connectedConnections = (connections ?? []).filter(c => c.status === "connected");
  const prefillOpen = prefillPatch !== null && !prefillDismissed;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <div>
            <CardTitle className="text-base">Publish Jobs</CardTitle>
            <CardDescription>
              承認・検証済みprobe patchのcommit/push/PR作成。Publishには常に明示的な承認が
              必要で、pushはサーバー生成の<code>probe/</code>branchにのみ行われます —
              default branchへのpushやforce pushは行いません。
            </CardDescription>
          </div>
          <Button
            size="sm" onClick={() => setShowCreate(true)}
            disabled={connectedConnections.length === 0}
            data-testid="new-publish-job-button"
          >
            <Plus className="h-4 w-4 mr-1" /> 新規Publish Job
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="w-64">
            <Select
              value={connectionFilter?.toString() ?? ""}
              onChange={e => setConnectionFilter(e.target.value ? Number(e.target.value) : null)}
              data-testid="publish-job-connection-filter"
            >
              <option value="">すべてのConnection</option>
              {(connections ?? []).map(c => (
                <option key={c.id} value={c.id}>{c.owner}/{c.repo}</option>
              ))}
            </Select>
          </div>

          {isLoading ? (
            <div className="space-y-2">{[1, 2].map(i => <Skeleton key={i} className="h-14 w-full" />)}</div>
          ) : !jobs?.length ? (
            <p className="text-sm text-muted-foreground text-center py-8">Publish Jobはまだありません。</p>
          ) : (
            <div className="space-y-2" data-testid="publish-jobs-list">
              {jobs.map(job => {
                const connection = connectionById.get(job.connection_id);
                return (
                  <button
                    key={job.id}
                    type="button"
                    onClick={() => setSelectedJobId(job.id)}
                    className="w-full text-left rounded-lg border p-3 hover:bg-accent/50 transition-colors"
                    data-testid={`publish-job-${job.id}`}
                  >
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <div className="flex items-center gap-2">
                        <Badge variant={publishJobStatusVariant(job.status)}>{job.status}</Badge>
                        <span className="text-sm">
                          {connection ? `${connection.owner}/${connection.repo}` : `connection #${job.connection_id}`}
                        </span>
                        {job.branch_name && <code className="text-xs text-muted-foreground">{job.branch_name}</code>}
                        {job.retry_count > 0 && (
                          <span className="text-xs text-muted-foreground">retries: {job.retry_count}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>base {shortSha(job.base_commit_sha)}</span>
                        {job.pr_url && (
                          <a
                            href={job.pr_url} target="_blank" rel="noreferrer"
                            className="inline-flex items-center gap-1 text-primary hover:underline"
                            onClick={e => e.stopPropagation()}
                          >
                            PR #{job.pr_number} <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                    </div>
                    {job.error && (
                      <div className="mt-1 text-xs text-destructive" data-testid={`publish-job-${job.id}-error`}>
                        {job.error}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <CreatePublishJobDialog
        open={showCreate || prefillOpen}
        onOpenChange={(open) => {
          setShowCreate(open);
          if (!open) setPrefillDismissed(true);
        }}
        connections={connectedConnections}
        defaultConnectionId={connectionFilter}
        prefillPatchId={prefillPatch?.id ?? null}
      />

      <PublishJobDetailDialog
        job={selectedJob}
        connection={selectedJob ? connectionById.get(selectedJob.connection_id) ?? null : null}
        onClose={() => setSelectedJobId(null)}
      />
    </div>
  );
}

function CreatePublishJobDialog({ open, onOpenChange, connections, defaultConnectionId, prefillPatchId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connections: GithubConnectionOut[];
  defaultConnectionId: number | null;
  prefillPatchId: number | null;
}) {
  const { data: patches } = useProbePatches();
  const createJob = useCreatePublishJob();
  const [connectionId, setConnectionId] = useState<number | null>(defaultConnectionId);
  // Issue #259: undefined = no manual selection yet, so `prefillPatchId` (from
  // `?patch=` in the URL, resolved by the caller) can still drive it -- the
  // same two-stage override pattern probe-planner.tsx uses for `?plan=`.
  // Once the developer touches the select (even back to blank), that choice
  // wins for the rest of this dialog's life.
  const [patchIdOverride, setPatchIdOverride] = useState<number | null | undefined>(undefined);
  const patchId = patchIdOverride !== undefined ? patchIdOverride : prefillPatchId;

  const readyPatches = (patches ?? []).filter(isPatchValidationGreen);

  const submit = async () => {
    if (!connectionId || !patchId) return;
    try {
      await createJob.mutateAsync({ connectionId, patchId });
      toast.success("Publish jobを作成しました — 準備完了後、承認待ちで一時停止します");
      onOpenChange(false);
      setPatchIdOverride(null);
    } catch (e) { toast.error(String(e)); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader><DialogTitle>新規Publish Job</DialogTitle></DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Connection</Label>
          <Select
            value={connectionId?.toString() ?? ""}
            onChange={e => setConnectionId(e.target.value ? Number(e.target.value) : null)}
            data-testid="publish-job-connection-select"
          >
            <option value="">Connectionを選択...</option>
            {connections.map(c => <option key={c.id} value={c.id}>{c.owner}/{c.repo}</option>)}
          </Select>
          {connections.length === 0 && (
            <p className="text-xs text-muted-foreground">検証済み（connected）のConnectionがまだありません。</p>
          )}
        </div>
        <div className="space-y-2">
          <Label>Probe patch</Label>
          {readyPatches.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              baseline・probed両方の検証がgreenのpatchがまだありません。先に{" "}
              <span className="font-medium">Probe Planner</span> ページでpatchを検証してください。
            </p>
          ) : (
            <Select
              value={patchId?.toString() ?? ""}
              onChange={e => setPatchIdOverride(e.target.value ? Number(e.target.value) : null)}
              data-testid="publish-job-patch-select"
            >
              <option value="">patchを選択...</option>
              {readyPatches.map(p => (
                <option key={p.id} value={p.id}>
                  Patch #{p.id} · plan #{p.plan_id} · {shortSha(p.commit_sha)}
                </option>
              ))}
            </Select>
          )}
        </div>
        <Button
          className="w-full" onClick={submit}
          disabled={createJob.isPending || !connectionId || !patchId}
        >
          {createJob.isPending ? "作成中..." : "Publish Jobを作成"}
        </Button>
      </div>
    </Dialog>
  );
}

function userLabel(userId: number | null, usernames: Map<number, string>): string {
  if (userId === null) return "—";
  return usernames.get(userId) ?? `User #${userId}`;
}

function PublishJobDetailDialog({ job, connection, onClose }: {
  job: PublishJobOut | null;
  connection: GithubConnectionOut | null;
  onClose: () => void;
}) {
  const { isAdmin } = useAuth();
  const { data: users } = useUsers();
  const { data: patches, isLoading: patchesLoading, isError: patchesError } = useProbePatches();
  const approve = useApprovePublishJob();
  const cancel = useCancelPublishJob();
  const retry = useRetryPublishJob();
  const { data: events } = usePublishJobEvents(job?.id ?? null);
  const [showApprove, setShowApprove] = useState(false);

  const usernames = useMemo(() => {
    const map = new Map<number, string>();
    if (isAdmin) (users ?? []).forEach(u => map.set(u.id, u.username));
    return map;
  }, [users, isAdmin]);

  if (!job) return null;
  const patch = patches?.find(p => p.id === job.patch_id) ?? null;
  const summary = job.validation_summary as Record<string, { overall_success?: boolean }> | null;

  return (
    <Dialog open={job !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogHeader>
        <DialogTitle>Publish job #{job.id}</DialogTitle>
      </DialogHeader>
      <div className="space-y-4" data-testid="publish-job-detail">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={publishJobStatusVariant(job.status)} data-testid="publish-job-detail-status">{job.status}</Badge>
          {connection && <span className="text-sm font-mono">{connection.owner}/{connection.repo}</span>}
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div><span className="text-muted-foreground">Base branch:</span> {job.base_branch}</div>
          <div><span className="text-muted-foreground">Base commit:</span> <code className="font-mono">{shortSha(job.base_commit_sha)}</code></div>
          <div><span className="text-muted-foreground">Publish branch:</span> {job.branch_name ?? "—"}</div>
          <div><span className="text-muted-foreground">Commit:</span> <code className="font-mono">{shortSha(job.commit_sha)}</code></div>
          <div><span className="text-muted-foreground">申請者:</span> {userLabel(job.requested_by_user_id, usernames)}</div>
          <div><span className="text-muted-foreground">承認者:</span> {userLabel(job.approved_by_user_id, usernames)}</div>
          {job.retry_count > 0 && (
            <div><span className="text-muted-foreground">Retry回数:</span> {job.retry_count}</div>
          )}
        </div>

        {connection && job.branch_name && (
          <div className="flex flex-wrap gap-3 text-xs">
            <a
              className="inline-flex items-center gap-1 text-primary hover:underline"
              href={`${connection.web_base_url}/${connection.owner}/${connection.repo}/tree/${job.branch_name}`}
              target="_blank" rel="noreferrer"
            >
              Branch <ExternalLink className="h-3 w-3" />
            </a>
            {job.commit_sha && (
              <a
                className="inline-flex items-center gap-1 text-primary hover:underline"
                href={`${connection.web_base_url}/${connection.owner}/${connection.repo}/commit/${job.commit_sha}`}
                target="_blank" rel="noreferrer"
              >
                Commit <ExternalLink className="h-3 w-3" />
              </a>
            )}
            {job.pr_url && (
              <a
                className="inline-flex items-center gap-1 text-primary hover:underline"
                href={job.pr_url} target="_blank" rel="noreferrer"
              >
                <GitPullRequest className="h-3 w-3" /> Pull Request #{job.pr_number} <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
        )}

        {summary && (
          <div className="space-y-1">
            <div className="text-xs font-medium">検証サマリ</div>
            <div className="flex gap-2 text-xs">
              {Object.entries(summary).map(([variant, result]) => (
                <Badge key={variant} variant={result.overall_success ? "success" : "destructive"}>
                  {variant}: {result.overall_success ? "PASS" : "FAIL"}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {job.error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-200" data-testid="publish-job-detail-error">
            {job.error}
          </div>
        )}

        {job.status === "manual_intervention_required" && (
          <div
            className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200"
            data-testid="publish-job-manual-intervention-note"
          >
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>
              remote branchが既存で、記録済みcommitと一致しません。単純な再試行では解決しない
              可能性があります — GitHub上でこのbranchの状態を確認してから再試行してください。
            </span>
          </div>
        )}

        {job.cleanup_state && job.cleanup_state !== "not_attempted" && job.cleanup_state !== "removed" && (
          <div className="text-xs text-amber-700 dark:text-amber-300">
            Worktree cleanup: {job.cleanup_state}{job.cleanup_error ? ` — ${job.cleanup_error}` : ""}
          </div>
        )}

        {job.status === "awaiting_approval" && patch?.diff && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">publish予定のPatch diff</summary>
            <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs font-mono max-h-64 overflow-y-auto">
              {patch.diff}
            </pre>
          </details>
        )}

        {/* Issue #267 item 5: publish_recovery.py's auto-retry/reconcile
            history was previously invisible in the Dashboard -- this reads
            the append-only publish_audit_events trail verbatim (no
            client-side interpretation). */}
        <details className="text-xs" data-testid="publish-job-events">
          <summary className="cursor-pointer text-muted-foreground flex items-center gap-1">
            <History className="h-3.5 w-3.5" /> イベント履歴{events?.length ? ` (${events.length})` : ""}
          </summary>
          <div className="mt-2 space-y-1.5 max-h-64 overflow-y-auto">
            {!events?.length ? (
              <p className="text-muted-foreground">イベントはまだありません。</p>
            ) : (
              events.map(ev => (
                <div key={ev.id} className="rounded-md border p-2" data-testid={`publish-job-event-${ev.id}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono">{ev.event_type}</span>
                    <span className="text-muted-foreground">{formatTimestamp(ev.created_at)}</span>
                  </div>
                  {ev.detail && (
                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                      {JSON.stringify(ev.detail)}
                    </pre>
                  )}
                </div>
              ))
            )}
          </div>
        </details>

        <div className="flex gap-2">
          {job.status === "awaiting_approval" && (
            <Button
              onClick={() => setShowApprove(true)}
              data-testid="publish-job-approve-button"
            >
              <CheckCircle2 className="h-4 w-4 mr-1" /> 承認
            </Button>
          )}
          {(job.status === "retryable_failed" || job.status === "manual_intervention_required") && (
            <Button
              variant={job.status === "manual_intervention_required" ? "outline" : "default"}
              onClick={() => retry.mutateAsync(job.id).then(() => toast.success("Publish jobを再試行しています")).catch(e => toast.error(String(e)))}
              disabled={retry.isPending}
              data-testid="publish-job-retry-button"
            >
              <RotateCcw className="h-4 w-4 mr-1" />
              {job.status === "manual_intervention_required" ? "再試行(要確認)" : "再試行"}
            </Button>
          )}
          {(job.status === "pending" || job.status === "awaiting_approval"
            || job.status === "retryable_failed" || job.status === "manual_intervention_required") && (
            <Button
              variant="outline"
              onClick={() => cancel.mutateAsync(job.id).then(() => toast.success("Publish jobをcancelしました")).catch(e => toast.error(String(e)))}
              disabled={cancel.isPending}
              data-testid="publish-job-cancel-button"
            >
              <XCircle className="h-4 w-4 mr-1" /> Cancel
            </Button>
          )}
        </div>
      </div>

      <Dialog open={showApprove} onOpenChange={setShowApprove}>
        <DialogHeader><DialogTitle>Publishを承認</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-100">
            検証済みpatchを新しいbranchにcommitし、{" "}
            <code className="font-mono">{connection?.owner}/{connection?.repo}</code> にpushした上で、
            <code className="font-mono">{job.base_branch}</code> に対してPull Requestを開きます。
            probe-agentはPRをmergeしません — GitHub上でレビューしてmergeしてください。
          </div>
          <div className="space-y-1 text-sm">
            <div>Target: <code className="font-mono">{connection?.owner}/{connection?.repo}</code></div>
            <div>Base branch: <code className="font-mono">{job.base_branch}</code> at <code className="font-mono">{shortSha(job.base_commit_sha)}</code></div>
            <div>New branch: <code className="font-mono">{job.branch_name ?? "(承認時に生成されます)"}</code></div>
          </div>

          {patchesLoading ? (
            <div className="space-y-1" data-testid="publish-job-confirm-diff-loading">
              <div className="text-xs text-muted-foreground">Patch diffを読み込み中...</div>
              <Skeleton className="h-16 w-full" />
            </div>
          ) : patch?.diff ? (
            <details className="text-xs" open data-testid="publish-job-confirm-diff">
              <summary className="cursor-pointer text-muted-foreground">publish予定のPatch diff</summary>
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs font-mono max-h-64 overflow-y-auto">
                {patch.diff}
              </pre>
            </details>
          ) : (
            <div
              className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200"
              data-testid="publish-job-confirm-diff-unavailable"
            >
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>
                {patchesError
                  ? "Patch diffの取得に失敗したため、承認できません。"
                  : "Patch diffを取得できないため、承認できません。"}
              </span>
            </div>
          )}

          <Button
            className="w-full"
            onClick={() => approve.mutateAsync(job.id).then(() => {
              toast.success("承認しました — publishを実行中です");
              setShowApprove(false);
            }).catch(e => toast.error(String(e)))}
            disabled={approve.isPending || !patch?.diff}
            data-testid="publish-job-confirm-approve-button"
          >
            {approve.isPending ? "承認中..." : "承認してpublish"}
          </Button>
        </div>
      </Dialog>
    </Dialog>
  );
}
