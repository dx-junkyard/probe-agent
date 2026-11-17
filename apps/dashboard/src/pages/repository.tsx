import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useRepositoryCandidates, useRepositoryConfig, useUpdateRepositoryConfig,
  useSnapshots, useLatestSnapshot, useCreateSnapshot, useSymbols, useIndexSymbols,
  useApiScanResult, useRunApiScan, useRepositoryStatus, useSystemState,
  useLatestRepositoryResync, useStartRepositoryResync, useSnapshotPreflight, sysKey,
} from "@/api/hooks";
import {
  SnapshotFreshnessBadge,
  SnapshotPreflightPanel,
  SnapshotProcessingBadge,
} from "@/components/snapshot-preflight";
import { useAuth } from "@/api/auth";
import {
  useDiagnosticFocus, useDiagnosticHighlight, DiagnosticFixCallout,
} from "@/components/diagnostic-fix";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { formatTimestamp, formatBytes } from "@/lib/utils";
import { GitCommit, FolderTree, Code2, RefreshCw, AlertTriangle, ScanSearch, Sparkles, GitBranch, CheckCircle2 } from "lucide-react";
import type { RepositoryCandidateOut, RepositoryConfigOut, RepositoryStatus } from "@/api/types";
import { SystemStateBanner } from "@/components/system-state";
import { useRepositoryConfiguredGate } from "@/components/repository-gate";
import { Link } from "react-router-dom";
import { useUnsavedChangesGuard } from "@/lib/ui-draft";

function patternsToText(patterns: string[] | undefined): string {
  return (patterns ?? []).join("\n");
}

function textToPatterns(text: string): string[] {
  return text.split("\n").map(l => l.trim()).filter(Boolean);
}

export default function RepositoryPage() {
  const { systemId } = useAuth();
  const { data: config, isLoading: configLoading } = useRepositoryConfig();
  const { data: candidates, isLoading: candidatesLoading } = useRepositoryCandidates();
  const updateConfig = useUpdateRepositoryConfig();
  const { data: snapshots, isLoading: snapsLoading } = useSnapshots();
  const { data: latestSnapshot } = useLatestSnapshot();
  const createSnapshot = useCreateSnapshot();
  const { data: symbolIndex, isLoading: symLoading } = useSymbols();
  const indexSymbols = useIndexSymbols();
  // Issue #255: reuse the already-fetched Refresh Hub status here so the
  // Symbols-tab button is gated by the same `!latest_snapshot` condition as
  // the Refresh Hub's own "Index symbols" button, instead of being clickable
  // with nothing to index.
  const { data: repoStatus } = useRepositoryStatus();
  const { data: systemState } = useSystemState();
  const pageItem = systemState?.page_items["/repository"]?.[0] ?? systemState?.primary_item;
  // Issue #258: gating "no ready snapshot yet" here would be a chicken-and-
  // egg deadlock -- creating the first snapshot IS the fix -- so this only
  // gates on the repository being definitively unconfigured (two-stage rule:
  // stays unblocked while status is loading/unknown).
  const configGate = useRepositoryConfiguredGate();
  // Issue #369: the recommended snapshot and its freshness come from the same
  // shared preflight the Experiment / Candidate / Replay surfaces read, so
  // this page cannot label a snapshot differently from the place it is used.
  const { data: preflight } = useSnapshotPreflight();
  const recommendedSnapshotId = preflight?.recommended_snapshot_id ?? null;

  const configKey = systemId != null ? `${systemId}-${config?.repo_path ?? ""}` : "empty";

  // Issue #115: when routed here from a diagnostic, open the tab that holds the
  // fix location and highlight it. The tab is controlled so a diagnostic can
  // select it without discarding in-progress form state, while normal tab
  // switching stays user-controlled.
  const focus = useDiagnosticFocus();
  const anchorTab =
    focus.anchor === "snapshot-create"
      ? "snapshots"
      : focus.anchor === "repo-config" || focus.anchor === "repo-patterns"
        ? "config"
        : null;

  // Controlled tab: a fresh diagnostic navigation (anchor/check changes) selects
  // the tab holding the fix, while later manual switching stays user-controlled.
  // State is adjusted during render (guarded by the focus token) rather than in
  // an effect, per the React "adjusting state when a prop changes" pattern.
  const [tab, setTab] = useState(anchorTab ?? "config");
  const focusToken = anchorTab ? `${focus.anchor}:${focus.checkId ?? ""}` : null;
  const [lastToken, setLastToken] = useState(focusToken);
  if (focusToken && focusToken !== lastToken) {
    setLastToken(focusToken);
    setTab(anchorTab!);
  }

  const snapshotHighlight = useDiagnosticHighlight<HTMLButtonElement>("snapshot-create");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Repository</h1>

      <SystemStateBanner
        item={pageItem}
        onAction={pageItem?.user_action_kind === "create_snapshot" ? () =>
          createSnapshot.mutateAsync().then(() => setTab("snapshots")).catch(e => toast.error(String(e))) : undefined}
        disabled={pageItem?.user_action_kind === "create_snapshot" && createSnapshot.isPending}
      />

      <RepositoryRefreshHub
        onCreateSnapshot={() =>
          createSnapshot.mutateAsync().then(s => {
            if (s.status === "failed") {
              toast.error(`Snapshotの作成に失敗しました: ${s.error_summary ?? "不明なエラー"}`);
            } else {
              toast.success("Snapshotを作成しました");
            }
            setTab("snapshots");
          }).catch(e => toast.error(String(e)))
        }
        creatingSnapshot={createSnapshot.isPending}
        onIndexSymbols={() =>
          indexSymbols.mutateAsync().then(() => {
            toast.success("symbol indexを更新しました");
            setTab("symbols");
          }).catch(e => toast.error(String(e)))
        }
        indexingSymbols={indexSymbols.isPending}
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="config">設定</TabsTrigger>
          <TabsTrigger value="snapshots">Snapshot</TabsTrigger>
          <TabsTrigger value="symbols">symbol</TabsTrigger>
          <TabsTrigger value="api-scan">API定義スキャン</TabsTrigger>
        </TabsList>

        {/* UX-03: 設定フォームの編集内容はタブ往復で失わない。 */}
        <TabsContent value="config" keepMounted>
          <DiagnosticFixCallout anchor="repo-config" className="mb-3" />
          <DiagnosticFixCallout anchor="repo-patterns" className="mb-3" />
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Repository設定</CardTitle>
              <CardDescription>解析の対象にするRepositoryを設定します</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {configLoading || candidatesLoading ? (
                <div className="space-y-3">{[1,2,3].map(i=><Skeleton key={i} className="h-10 w-full"/>)}</div>
              ) : (
                <RepoConfigForm
                  key={configKey}
                  config={config ?? null}
                  candidates={candidates ?? []}
                  onSave={async (data) => {
                    await updateConfig.mutateAsync(data);
                    toast.success("Repository設定を保存しました");
                  }}
                  isPending={updateConfig.isPending}
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="snapshots">
          <DiagnosticFixCallout anchor="snapshot-create" className="mb-3" />
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">Snapshot</CardTitle>
                <CardDescription>ある時点のcommit済みRepositoryを固定した記録です</CardDescription>
              </div>
              <Button
                {...snapshotHighlight}
                size="sm"
                variant="outline"
                onClick={() => createSnapshot.mutateAsync().then(s => {
                  if (s.status === "failed") {
                    toast.error(`Snapshotの作成に失敗しました: ${s.error_summary ?? "不明なエラー"}`);
                  } else {
                    toast.success("Snapshotを作成しました", {
                      description: s.warnings?.length ? `警告が${s.warnings.length}件あります` : "すべてのファイルを取り込みました。",
                    });
                  }
                }).catch(e => toast.error(String(e)))}
                disabled={createSnapshot.isPending || configGate.blocked}
                title={configGate.blocked ? [configGate.summary, configGate.remediation].filter(Boolean).join(" ") : undefined}
              >
                <RefreshCw className={`h-4 w-4 mr-1 ${createSnapshot.isPending ? "animate-spin" : ""}`} />
                Snapshotのみ作成（詳細操作）
              </Button>
            </CardHeader>
            <CardContent>
              {/* Issue #369: one shared statement of where the recommended
                  snapshot stands, in the same words every other surface uses. */}
              <div className="mb-3">
                <SnapshotPreflightPanel preflight={preflight} />
              </div>
              {configGate.blocked && (
                <p className="text-xs text-muted-foreground mb-3" data-testid="create-snapshot-not-configured-reason">
                  {configGate.summary} {configGate.remediation}{" "}
                  {configGate.to && (
                    <Link to={configGate.to} className="underline">{configGate.actionLabel ?? "Repository設定へ"}</Link>
                  )}
                </p>
              )}
              {snapsLoading ? (
                <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-16 w-full"/>)}</div>
              ) : !snapshots?.length ? (
                <p className="text-sm text-muted-foreground text-center py-8">Snapshotはまだありません</p>
              ) : (
                <div className="space-y-3">
                  {snapshots.map(s => (
                    <div key={s.id} className="rounded-lg border p-4 space-y-2">
                      <div className="flex items-start justify-between">
                        <div className="space-y-1">
                          {/* Issue #369: two axes, two badges. `status` says
                              whether the analysis finished; freshness says
                              whether the pinned commit still equals HEAD. A
                              ready snapshot can be stale, so one badge cannot
                              carry both — that conflation was the bug. */}
                          <div className="flex flex-wrap items-center gap-2">
                            <GitCommit className="h-4 w-4 text-muted-foreground" />
                            <span className="font-mono text-xs">{s.commit_sha?.slice(0, 8)}</span>
                            <SnapshotProcessingBadge state={s.status} />
                            {s.id === recommendedSnapshotId ? (
                              <>
                                <SnapshotFreshnessBadge
                                  freshness={preflight?.freshness ?? "unknown"}
                                />
                                <Badge variant="default" data-testid="snapshot-recommended">推奨</Badge>
                              </>
                            ) : (
                              <span className="text-xs text-muted-foreground">再現用途</span>
                            )}
                          </div>
                          <div className="flex items-center gap-4 text-xs text-muted-foreground">
                            <span><FolderTree className="inline h-3 w-3 mr-1" />{s.file_count}ファイル</span>
                            <span>合計 {formatBytes(s.total_size)}</span>
                            {s.indexed_size > 0 && s.indexed_size !== s.total_size && (
                              <span>取り込み {formatBytes(s.indexed_size)}</span>
                            )}
                            {s.metadata_only_count > 0 && (
                              <span className="text-yellow-600">内容省略 {s.metadata_only_count}件</span>
                            )}
                            <span>{formatTimestamp(s.created_at)}</span>
                          </div>
                        </div>
                      </div>
                      {s.status === "failed" && s.error_summary && (
                        <div className="flex items-start gap-2 text-xs text-destructive bg-destructive/10 rounded p-2">
                          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                          <span>{s.error_summary}</span>
                        </div>
                      )}
                      {s.warnings?.length > 0 && (
                        <div className="space-y-1">
                          {s.warnings.map((w, i) => (
                            <div key={i} className="flex items-start gap-2 text-xs text-yellow-700 bg-yellow-50 dark:bg-yellow-900/20 dark:text-yellow-400 rounded p-2">
                              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                              <span>{w}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {latestSnapshot?.id === s.id && latestSnapshot.files.length > 0 && (
                        <details className="text-xs">
                          <summary className="cursor-pointer text-muted-foreground">
                            取り込んだファイルと省略したファイルを確認
                          </summary>
                          <div className="mt-2 max-h-64 space-y-1 overflow-y-auto rounded border p-2">
                            {(() => {
                              const DISPLAY_LIMIT = 100;
                              const problemFiles = latestSnapshot.files.filter(f => f.inclusion_status !== 'indexed');
                              const indexedFiles = latestSnapshot.files.filter(f => f.inclusion_status === 'indexed');

                              const indexedToShowCount = Math.max(0, DISPLAY_LIMIT - problemFiles.length);
                              const indexedToShow = indexedFiles.slice(0, indexedToShowCount);
                              const filesToShow = [...problemFiles, ...indexedToShow];
                              const omittedCount = indexedFiles.length - indexedToShow.length;

                              return (
                                <>
                                  {filesToShow.map(file => (
                                    <div key={file.path} className="flex items-start justify-between gap-3">
                                      <span className="min-w-0 truncate font-mono" title={file.path}>{file.path}</span>
                                      <span className="shrink-0 text-muted-foreground">
                                        {file.inclusion_status}
                                        {file.exclusion_reason ? ` — ${file.exclusion_reason}` : ""}
                                      </span>
                                    </div>
                                  ))}
                                  {omittedCount > 0 && (
                                    <p className="text-muted-foreground text-xs italic mt-2">
                                      ほかに取り込んだファイルが{omittedCount}件あります（この一覧では省略しています）。
                                    </p>
                                  )}
                                </>
                              );
                            })()}
                          </div>
                        </details>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="symbols">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">コードのsymbol</CardTitle>
                <CardDescription>indexした関数・クラス・モジュールです</CardDescription>
              </div>
              <Button
                size="sm"
                onClick={() => indexSymbols.mutateAsync().then(() => toast.success("symbol indexを更新しました")).catch(e => toast.error(String(e)))}
                disabled={indexSymbols.isPending || !repoStatus?.latest_snapshot}
                title={!repoStatus?.latest_snapshot ? "symbolをindexする前にSnapshotを作成してください" : undefined}
              >
                <Code2 className={`h-4 w-4 mr-1 ${indexSymbols.isPending ? "animate-spin" : ""}`} />
                symbol indexを更新
              </Button>
            </CardHeader>
            <CardContent>
              {!repoStatus?.latest_snapshot && (
                <p className="text-xs text-muted-foreground mb-3" data-testid="index-symbols-no-snapshot-reason">
                  Snapshotがまだありません。symbolをindexする前に、SnapshotタブでSnapshotを作成してください。
                </p>
              )}
              {symLoading ? (
                <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-10 w-full"/>)}</div>
              ) : !symbolIndex?.symbols?.length ? (
                <p className="text-sm text-muted-foreground text-center py-8">indexしたsymbolはまだありません</p>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground mb-4">{symbolIndex.symbol_count}件のsymbolをindexしました</p>
                  <div className="overflow-x-auto max-h-96 overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-card">
                        <tr className="border-b text-left">
                          <th className="pb-2 font-medium text-muted-foreground">symbol</th>
                          <th className="pb-2 font-medium text-muted-foreground">種類</th>
                          <th className="pb-2 font-medium text-muted-foreground">パス</th>
                          <th className="pb-2 font-medium text-muted-foreground text-right">行</th>
                        </tr>
                      </thead>
                      <tbody>
                        {symbolIndex.symbols.map(s => (
                          <tr key={s.id} className="border-b last:border-0">
                            <td className="py-2 font-mono text-xs">{s.qualified_name}</td>
                            <td className="py-2"><Badge variant="outline">{s.kind}</Badge></td>
                            <td className="py-2 text-xs text-muted-foreground">{s.path}</td>
                            <td className="py-2 text-right text-xs">{s.start_line}–{s.end_line}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="api-scan">
          <ApiScanPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "—";
}

function repositoryLagLabel(status: RepositoryStatus): string {
  // B7: 比べる Snapshot が無いときは「差分を確認できません」ではなく、無いと言う。
  if (!status.latest_snapshot) return "Snapshot未作成";
  if (status.head_relation === "same") return "最新";
  if (status.head_relation === "behind" && status.commits_behind != null) {
    return `${status.commits_behind} commit遅れ`;
  }
  if (status.head_relation === "diverged") return "履歴が分岐しています";
  // B7 (続き): 比べられなかった理由が最新 Snapshot の処理状態 (#369 の
  // processing state、サーバー応答の `latest_snapshot.status`) にあるなら、
  // その事実を言う。
  if (status.latest_snapshot.status === "failed") return "最新Snapshotの作成に失敗";
  if (status.latest_snapshot.status === "indexing") return "Snapshot作成中";
  return "差分を確認できません";
}

function repositoryNextActionLabel(action: string): string {
  const labels: Record<string, string> = {
    "Configure the target repository.": "対象Repositoryを設定してください。",
    "Working tree is dirty; commit, stash, or revert before relying on patch apply.": "working treeに未commit変更があります。patch適用前にcommit・stash・revertしてください。",
    "Create a snapshot of the current HEAD.": "現在のHEADからSnapshotを作成してください。",
    "Repository HEAD changed; create a new snapshot before generating new analysis or patches.": "RepositoryのHEADが変わりました。新しい解析やpatchを生成する前にSnapshotを更新してください。",
    "Re-index symbols for the latest snapshot.": "最新Snapshotのsymbol indexを更新してください。",
  };
  return labels[action] ?? action;
}

function RepositoryRefreshHub({
  onCreateSnapshot, creatingSnapshot, onIndexSymbols, indexingSymbols,
}: {
  onCreateSnapshot: () => void;
  creatingSnapshot: boolean;
  onIndexSymbols: () => void;
  indexingSymbols: boolean;
}) {
  const { data: status, isLoading, refetch, isFetching } = useRepositoryStatus();
  const { data: resyncJob } = useLatestRepositoryResync();
  const startResync = useStartRepositoryResync();
  const queryClient = useQueryClient();
  const refreshedTerminalJob = useRef<number | null>(null);

  const resyncActive = resyncJob?.status === "queued"
    || resyncJob?.status === "snapshotting"
    || resyncJob?.status === "indexing";
  const resyncTerminal = resyncJob?.status === "completed"
    || resyncJob?.status === "snapshot_failed"
    || resyncJob?.status === "index_failed";

  useEffect(() => {
    if (!resyncJob || !resyncTerminal || refreshedTerminalJob.current === resyncJob.id) return;
    refreshedTerminalJob.current = resyncJob.id;
    void refetch();
    for (const key of ["snapshots", "latestSnapshot", "symbols", "system-state", "capabilityHierarchy"]) {
      void queryClient.invalidateQueries({ queryKey: sysKey(key) });
    }
  }, [queryClient, refetch, resyncJob, resyncTerminal]);

  if (isLoading) {
    return <Card><CardContent className="py-6"><Skeleton className="h-24 w-full" /></CardContent></Card>;
  }
  if (!status) return null;

  if (!status.configured) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <GitBranch className="h-4 w-4" /> Repository更新
          </CardTitle>
          <CardDescription>更新を開始するには対象Repositoryを設定してください。</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const fresh = !status.snapshot_stale && !status.symbols_stale && !status.working_tree_dirty && !status.head_error;
  const showResyncJob = !!resyncJob && (
    resyncJob.status !== "completed"
    || resyncJob.snapshot_id === status.latest_snapshot?.id
  );
  // B5: 準備ができた後は「システムを理解する」が次の作業。古い Capability が
  // ある (既存の「System Understandingを構築」が出る) とき、または Snapshot /
  // symbol がまた古くなったときは、更新ボタンが主ボタンのまま。
  const showInterviewCta = showResyncJob
    && resyncJob?.status === "completed"
    && resyncJob.stale_capability_count === 0
    && !status.snapshot_stale
    && !status.symbols_stale;

  return (
    <Card data-testid="refresh-hub">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="text-base flex items-center gap-2">
            <GitBranch className="h-4 w-4" /> Repository更新
          </CardTitle>
          <CardDescription>
            Repositoryに対して解析が古くなっていないか確認し、必要な更新を順番に実行します。
            HEADとworking treeを読み取るだけで、fetch・pull・Repositoryへの書き込みは行いません。
          </CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={`h-4 w-4 mr-1 ${isFetching ? "animate-spin" : ""}`} />
          状態を更新
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 text-sm">
          <div className="space-y-0.5">
            <div className="text-xs text-muted-foreground">現在のHEAD</div>
            <div className="font-mono">{shortSha(status.current_head)}</div>
          </div>
          <div className="space-y-0.5">
            <div className="text-xs text-muted-foreground">最新Snapshot</div>
            <div className="font-mono flex items-center gap-1">
              {status.latest_snapshot
                ? <>#{status.latest_snapshot.id} <span className="text-muted-foreground">{shortSha(status.latest_snapshot.commit_sha)}</span></>
                : "なし"}
            </div>
          </div>
          <div className="space-y-0.5">
            <div className="text-xs text-muted-foreground">symbol index</div>
            <div className="font-mono">
              {status.latest_indexed_snapshot ? `#${status.latest_indexed_snapshot.id}` : "なし"}
            </div>
          </div>
          <div className="space-y-0.5">
            <div className="text-xs text-muted-foreground">Understanding構築</div>
            <div className="font-mono">
              {status.understanding_snapshot_id
                ? `#${status.understanding_snapshot_id} (${status.understanding_status ?? "?"})`
                : "なし"}
            </div>
          </div>
          <div className="space-y-0.5">
            <div className="text-xs text-muted-foreground">SnapshotとHEADの差</div>
            <div className="font-medium" data-testid="repository-lag">
              {repositoryLagLabel(status)}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {fresh && (
            <Badge variant="success" className="gap-1">
              <CheckCircle2 className="h-3 w-3" /> 最新です
            </Badge>
          )}
          {/* UX 監査 2026-11-17 B7: Snapshot が無いことと Snapshot が古いことは
              別の事実。サーバーの `snapshot_stale` は「ready な Snapshot が無い」
              場合にも true になるので、同じ応答の `latest_snapshot` の有無で
              表示を分ける (新しい判定は作らない)。 */}
          {status.snapshot_stale && !status.latest_snapshot && (
            <Badge variant="warning" data-testid="snapshot-missing-badge">Snapshot未作成</Badge>
          )}
          {/* B7 (続き): 最新の Snapshot が失敗・作成中なのは「HEADより古い」とは
              別の事実。サーバーの `latest_snapshot.status` (ready / indexing /
              failed) だけで分ける。 */}
          {status.snapshot_stale && status.latest_snapshot?.status === "failed" && (
            <Badge variant="destructive" data-testid="snapshot-failed-badge">最新Snapshotの作成に失敗</Badge>
          )}
          {status.snapshot_stale && status.latest_snapshot?.status === "indexing" && (
            <Badge variant="warning" data-testid="snapshot-indexing-badge">Snapshot作成中</Badge>
          )}
          {status.snapshot_stale && status.latest_snapshot
            && status.latest_snapshot.status !== "failed"
            && status.latest_snapshot.status !== "indexing" && (
            <Badge variant="destructive" data-testid="snapshot-stale-badge">SnapshotがHEADより古い</Badge>
          )}
          {status.symbols_stale && !status.snapshot_stale && (
            <Badge variant="warning">symbolが古い</Badge>
          )}
          {status.working_tree_dirty && (
            <Badge variant="warning" data-testid="dirty-badge">
              working treeに未commit変更あり（{status.dirty_file_count}件）
            </Badge>
          )}
          {status.head_error && <Badge variant="destructive">HEADを読み取れません</Badge>}
        </div>

        {status.head_error && (
          <p className="text-xs text-destructive">{status.head_error}</p>
        )}

        {status.snapshot_stale && (
          <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
            {status.latest_snapshot?.status === "failed"
              ? <>最新のSnapshot #{status.latest_snapshot.id}（<code>{shortSha(status.latest_snapshot.commit_sha)}</code>）の作成に失敗しました。原因を確認してSnapshotを作成し直してください。RepositoryのHEADは<code>{shortSha(status.current_head)}</code>です。</>
              : status.latest_snapshot?.status === "indexing"
              ? <>Snapshot #{status.latest_snapshot.id}（<code>{shortSha(status.latest_snapshot.commit_sha)}</code>）を作成しています。完了するまでお待ちください。</>
              : status.latest_snapshot
              ? <>現在の解析はSnapshot #{status.latest_snapshot.id}（<code>{shortSha(status.latest_snapshot.commit_sha)}</code>）に基づいていますが、RepositoryのHEADは<code>{shortSha(status.current_head)}</code>です。新しいpatchを生成する前にSnapshotを更新してください。</>
              : <>HEAD <code>{shortSha(status.current_head)}</code>のSnapshotがまだありません。最初のSnapshotを作成して解析を開始してください。</>}
          </div>
        )}

        {status.working_tree_dirty && status.dirty_sample.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              未commit変更が{status.dirty_file_count}件あります — patch適用前にcommit・stash・revertしてください
            </summary>
            <pre className="mt-2 rounded bg-muted p-2 overflow-x-auto">{status.dirty_sample.join("\n")}</pre>
          </details>
        )}

        {status.next_actions.length > 0 && (
          <div className="text-xs">
            <div className="font-medium mb-1">次のステップ</div>
            <ol className="list-decimal pl-5 space-y-0.5 text-muted-foreground">
              {status.next_actions.map((a, i) => <li key={i}>{repositoryNextActionLabel(a)}</li>)}
            </ol>
          </div>
        )}

        {showResyncJob && resyncJob && (
          <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs space-y-1" data-testid="resync-job-status">
            {resyncActive && (
              <div className="flex items-center gap-2">
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                <span>
                  {resyncJob.status === "indexing"
                    ? `Snapshot #${resyncJob.snapshot_id ?? "…"} のsymbolをindex中`
                    : "commit済みRepositoryからSnapshotを作成中"}
                </span>
              </div>
            )}
            {resyncJob.status === "completed" && (
              <div className="space-y-1">
                <div className="text-emerald-700 dark:text-emerald-300">
                  Snapshot #{resyncJob.snapshot_id} とsymbol indexの準備ができました。
                </div>
                {/* UX 監査 2026-11-17 B5: 準備ができた後の次の作業への導線。遷移で
                    あって実行ではない (Overview の next_action と同じ行き先)。古い
                    Capability がある場合は下の「System Understandingを構築」が
                    次の作業なので、導線を 2 つ並べない。 */}
                {showInterviewCta && (
                  <div className="pt-1">
                    <Link
                      to="/interview"
                      className={buttonVariants({ size: "sm" })}
                      data-testid="resync-next-interview-cta"
                    >
                      システムを理解する
                    </Link>
                  </div>
                )}
                {resyncJob.stale_capability_count > 0 && (
                  <div data-testid="stale-capability-guidance">
                    <span>{resyncJob.stale_capability_count}件のCapabilityが古いSnapshotを参照しています。 </span>
                    <Link className="text-primary hover:underline" to="/capability-map">Capability Mapを確認</Link>
                    <span> · </span>
                    <Link className="text-primary hover:underline" to="/system-understanding?fix=build">System Understandingを構築</Link>
                  </div>
                )}
              </div>
            )}
            {resyncJob.status === "snapshot_failed" && (
              <div className="text-destructive">Snapshot作成に失敗しました: {resyncJob.error ?? "不明なエラー"}</div>
            )}
            {resyncJob.status === "index_failed" && (
              <div className="text-destructive">
                保持したSnapshot #{resyncJob.snapshot_id} のsymbol indexに失敗しました: {resyncJob.error ?? "不明なエラー"}
              </div>
            )}
          </div>
        )}

        {/* One recommended action. Snapshot-only / index-only operations are
            retained for recovery and reproduction, but disclosed as advanced
            operations so they no longer compete with the normal path. */}
        <div className="space-y-2">
          <Button
            size="sm"
            // B5: 次の作業への CTA が出ている間は主ボタンを 2 つ並べない。
            variant={showInterviewCta ? "outline" : "default"}
            data-testid="repository-resync-button"
            onClick={() => startResync.mutateAsync()
              .then(() => toast.success("Repositoryの更新を開始しました"))
              .catch((e) => toast.error(String(e)))}
            disabled={startResync.isPending || resyncActive}
          >
            <RefreshCw className={`h-4 w-4 mr-1 ${startResync.isPending || resyncActive ? "animate-spin" : ""}`} />
            {resyncActive ? "Snapshotとsymbolを更新中" : "Snapshotとsymbolをまとめて更新"}
          </Button>
          <p className="text-xs text-muted-foreground">通常はこの操作だけで、推奨Snapshotの作成とsymbol index更新が完了します。</p>
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground underline">復旧・再現用の個別操作</summary>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={onCreateSnapshot} disabled={creatingSnapshot}>
                <RefreshCw className={`h-4 w-4 mr-1 ${creatingSnapshot ? "animate-spin" : ""}`} />
                {status.snapshot_stale ? "Snapshotのみ作成" : "Snapshotのみ再作成"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={onIndexSymbols}
                disabled={indexingSymbols || !status.latest_snapshot}
              >
                <Code2 className={`h-4 w-4 mr-1 ${indexingSymbols ? "animate-spin" : ""}`} />
                symbol indexのみ更新
              </Button>
            </div>
          </details>
        </div>
      </CardContent>
    </Card>
  );
}

function ApiScanPanel() {
  const { data: scan, isLoading } = useApiScanResult();
  const runScan = useRunApiScan();

  const onScan = () => {
    runScan.mutateAsync()
      .then((r) => {
        if (r.status === "completed") {
          toast.success(`スキャン完了: ${r.patterns.length}件のパターンから${r.extracted_count}件のAPI endpointを抽出しました`);
        } else {
          toast.error(r.error || "API定義スキャンに失敗しました");
        }
      })
      .catch((e) => toast.error(String(e)));
  };

  const result = runScan.data ?? scan;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="text-base flex items-center gap-2">
            <ScanSearch className="h-4 w-4" /> API定義スキャン
          </CardTitle>
          <CardDescription>
            reasoning modelで、framework・言語を問わずAPI定義の場所を探し、それを
            抽出する正規表現を生成します。正規表現は固定したSnapshotへ決定的に適用
            します。reasoning modelの設定が必要です。結果はLLMが生成したものなので、
            信頼する前に確認してください。
          </CardDescription>
        </div>
        <Button size="sm" onClick={onScan} disabled={runScan.isPending}>
          <Sparkles className={`h-4 w-4 mr-1 ${runScan.isPending ? "animate-spin" : ""}`} />
          {runScan.isPending ? "スキャン中…" : "API定義をスキャン"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-10 w-full"/>)}</div>
        ) : !result || result.status === "none" ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            API定義スキャンはまだ実行していません。Snapshotを作成してからスキャンすると、
            決定的なindexerが対応していないframeworkのAPIも検出できます。
          </p>
        ) : result.status === "failed" ? (
          <div className="rounded-md border border-red-300 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-4 py-3 text-sm text-red-800 dark:text-red-200">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="h-4 w-4" /> スキャンに失敗しました
              {result.is_mock && <Badge variant="outline" className="text-[10px]">mock</Badge>}
            </div>
            <p className="mt-1">{result.error}</p>
            <p className="mt-1 text-xs">
              API定義スキャンには実際のreasoning modelが必要です
              （INTELLIGENCE_LLM_PROVIDER / INTELLIGENCE_LLM_MODEL、または LLM_PROVIDER /
              LLM_MODEL を設定してください）。ヒューリスティックな代替処理は行いません。
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge variant="secondary">{result.extracted_count}件のAPI endpoint</Badge>
              <span className="text-muted-foreground">
                {result.patterns.length}件のパターン
                {result.provider && ` · ${result.provider}/${result.model}`}
              </span>
              {result.frameworks.map(f => (
                <Badge key={f} variant="outline" className="text-[10px]">{f}</Badge>
              ))}
              {result.is_mock && <Badge variant="outline" className="text-[10px]">mock</Badge>}
            </div>
            {result.diagnostics.length > 0 && (
              <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-1">
                {result.diagnostics.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            )}
            <div className="space-y-2">
              {result.patterns.map((p, i) => (
                <div key={p.id ?? i} className="rounded-md border p-3 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="outline" className="text-[10px]">{p.framework}</Badge>
                    <Badge variant="outline" className="text-[10px]">{p.language}</Badge>
                    <span className="text-xs text-muted-foreground">{p.file_glob}</span>
                    <span className="ml-auto text-xs">{p.match_count}件一致</span>
                    <span className="text-xs text-muted-foreground">{Math.round(p.confidence * 100)}%</span>
                  </div>
                  <code className="block text-xs font-mono bg-muted/50 rounded px-2 py-1 break-all">{p.regex}</code>
                  <p className="text-xs text-muted-foreground">{p.reason}</p>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              抽出したendpointは<span className="font-medium">Flow Explorer</span>の
              API一覧に、LLM由来として表示されます。確認のための表示であり、LLMだけが
              見つけたendpointを起点にした処理ツリーはまだ作れません。
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RepoConfigForm({ config, candidates, onSave, isPending }: {
  config: RepositoryConfigOut | null;
  candidates: RepositoryCandidateOut[];
  onSave: (data: { repo_path: string; include_patterns: string[]; exclude_patterns: string[] }) => Promise<void>;
  isPending: boolean;
}) {
  const [repoPath, setRepoPath] = useState(config?.repo_path ?? "");
  const [includePatterns, setIncludePatterns] = useState(patternsToText(config?.include_patterns));
  const [excludePatterns, setExcludePatterns] = useState(patternsToText(config?.exclude_patterns));
  // UX-03: 保存済み設定との差分を未保存入力として、アプリ共通の破棄確認
  // (System 切替・ログアウト・画面離脱) に参加させる。
  const { systemId } = useAuth();
  // Issue #466 review R4: 保存時と同じ正規化 (`textToPatterns`: 前後空白・
  // 空行の除去) をかけた値どうしで比べる。生の文字列で比べると、末尾改行や
  // 空行を含む入力は保存に成功しても「未保存」のまま残り、離脱のたびに
  // 破棄確認が出続ける。
  const samePatterns = (text: string, saved: string[] | undefined) =>
    JSON.stringify(textToPatterns(text)) === JSON.stringify(saved ?? []);
  const dirty =
    repoPath !== (config?.repo_path ?? "")
    || !samePatterns(includePatterns, config?.include_patterns)
    || !samePatterns(excludePatterns, config?.exclude_patterns);
  useUnsavedChangesGuard(`repository-config:system-${systemId ?? "none"}`, dirty);
  const repoPathHighlight = useDiagnosticHighlight<HTMLDivElement>("repo-config");
  const patternsHighlight = useDiagnosticHighlight<HTMLDivElement>("repo-patterns");

  const handleSave = async () => {
    try {
      await onSave({
        repo_path: repoPath,
        include_patterns: textToPatterns(includePatterns),
        exclude_patterns: textToPatterns(excludePatterns),
      });
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <>
      <div {...repoPathHighlight} className={`space-y-2 ${repoPathHighlight.className}`}>
        <Label htmlFor="repo-config-path">Repository</Label>
        <Select id="repo-config-path" value={repoPath} onChange={e => setRepoPath(e.target.value)}>
          <option value="">Repositoryを選択...</option>
          {config?.repo_path && !candidates.some(candidate => candidate.path === config.repo_path) && (
            <option value={config.repo_path} disabled>
              利用できません: {config.repo_path}
            </option>
          )}
          {candidates.map(candidate => (
            <option key={candidate.path} value={candidate.path}>
              {candidate.name} — {candidate.path}
            </option>
          ))}
        </Select>
        {!candidates.length && (
          // UX 監査 2026-11-17 B2: 原因だけでなく解決手順を同じ場所に出す。
          <div className="space-y-1 text-xs" data-testid="repo-candidates-empty">
            <p className="text-destructive">
              設定されたRepositoryルートの下にGitリポジトリが見つかりませんでした。
            </p>
            <p className="text-muted-foreground" data-testid="repo-candidates-empty-remediation">
              Control Serverの環境変数 <code>PROBE_REPOSITORY_ROOTS</code> に、Gitリポジトリを含む親ディレクトリの絶対パスを設定し、Control Serverを再起動してください。
            </p>
          </div>
        )}
      </div>
      <div {...patternsHighlight} className={`space-y-4 ${patternsHighlight.className}`}>
        <div className="space-y-2">
          <Label htmlFor="repo-config-include">対象パターン（Include） <span className="text-muted-foreground font-normal">（1行に1つ）</span></Label>
          <Textarea id="repo-config-include" value={includePatterns} onChange={e => setIncludePatterns(e.target.value)} placeholder={"*.py\n*.js"} rows={3} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="repo-config-exclude">除外パターン（Exclude） <span className="text-muted-foreground font-normal">（1行に1つ）</span></Label>
          <Textarea id="repo-config-exclude" value={excludePatterns} onChange={e => setExcludePatterns(e.target.value)} placeholder={"test_*\n__pycache__"} rows={3} />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={handleSave} disabled={isPending || !repoPath || !candidates.some(candidate => candidate.path === repoPath)}>
          {isPending ? "保存中..." : "設定を保存"}
        </Button>
        {dirty && (
          <span className="text-xs text-amber-700 dark:text-amber-400" role="status" data-testid="repo-config-unsaved">
            未保存の変更があります
          </span>
        )}
      </div>
    </>
  );
}
