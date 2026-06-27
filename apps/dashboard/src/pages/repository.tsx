import { useState } from "react";
import {
  useRepositoryCandidates, useRepositoryConfig, useUpdateRepositoryConfig,
  useSnapshots, useLatestSnapshot, useCreateSnapshot, useSymbols, useIndexSymbols,
  useApiScanResult, useRunApiScan,
} from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { formatTimestamp, formatBytes } from "@/lib/utils";
import { GitCommit, FolderTree, Code2, RefreshCw, AlertTriangle, ScanSearch, Sparkles } from "lucide-react";
import type { RepositoryCandidateOut, RepositoryConfigOut } from "@/api/types";

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

  const configKey = systemId != null ? `${systemId}-${config?.repo_path ?? ""}` : "empty";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Repository</h1>

      <Tabs defaultValue="config">
        <TabsList>
          <TabsTrigger value="config">Configuration</TabsTrigger>
          <TabsTrigger value="snapshots">Snapshots</TabsTrigger>
          <TabsTrigger value="symbols">Symbols</TabsTrigger>
          <TabsTrigger value="api-scan">API Scan</TabsTrigger>
        </TabsList>

        <TabsContent value="config">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Repository Configuration</CardTitle>
              <CardDescription>Configure the target repository for analysis</CardDescription>
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
                    toast.success("Repository config saved");
                  }}
                  isPending={updateConfig.isPending}
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="snapshots">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">Snapshots</CardTitle>
                <CardDescription>Point-in-time snapshots of the repository</CardDescription>
              </div>
              <Button
                size="sm"
                onClick={() => createSnapshot.mutateAsync().then(s => {
                  if (s.status === "failed") {
                    toast.error(`Snapshot failed: ${s.error_summary ?? "unknown error"}`);
                  } else {
                    toast.success("Snapshot created", {
                      description: s.warnings?.length ? `with ${s.warnings.length} warning(s)` : "All files indexed successfully.",
                    });
                  }
                }).catch(e => toast.error(String(e)))}
                disabled={createSnapshot.isPending}
              >
                <RefreshCw className={`h-4 w-4 mr-1 ${createSnapshot.isPending ? "animate-spin" : ""}`} />
                Create Snapshot
              </Button>
            </CardHeader>
            <CardContent>
              {snapsLoading ? (
                <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-16 w-full"/>)}</div>
              ) : !snapshots?.length ? (
                <p className="text-sm text-muted-foreground text-center py-8">No snapshots yet</p>
              ) : (
                <div className="space-y-3">
                  {snapshots.map(s => (
                    <div key={s.id} className="rounded-lg border p-4 space-y-2">
                      <div className="flex items-start justify-between">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <GitCommit className="h-4 w-4 text-muted-foreground" />
                            <span className="font-mono text-xs">{s.commit_sha?.slice(0, 8)}</span>
                            <Badge variant={s.status === "ready" ? "success" : s.status === "failed" ? "destructive" : "secondary"}>
                              {s.status}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-4 text-xs text-muted-foreground">
                            <span><FolderTree className="inline h-3 w-3 mr-1" />{s.file_count} files</span>
                            <span>{formatBytes(s.total_size)} total</span>
                            {s.indexed_size > 0 && s.indexed_size !== s.total_size && (
                              <span>{formatBytes(s.indexed_size)} indexed</span>
                            )}
                            {s.metadata_only_count > 0 && (
                              <span className="text-yellow-600">{s.metadata_only_count} content omitted</span>
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
                            Inspect indexed and omitted files
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
                                      {omittedCount} additional indexed file(s) omitted from this view.
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
                <CardTitle className="text-base">Code Symbols</CardTitle>
                <CardDescription>Indexed functions, classes, and modules</CardDescription>
              </div>
              <Button
                size="sm"
                onClick={() => indexSymbols.mutateAsync().then(() => toast.success("Symbols indexed")).catch(e => toast.error(String(e)))}
                disabled={indexSymbols.isPending}
              >
                <Code2 className={`h-4 w-4 mr-1 ${indexSymbols.isPending ? "animate-spin" : ""}`} />
                Index Symbols
              </Button>
            </CardHeader>
            <CardContent>
              {symLoading ? (
                <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-10 w-full"/>)}</div>
              ) : !symbolIndex?.symbols?.length ? (
                <p className="text-sm text-muted-foreground text-center py-8">No symbols indexed yet</p>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground mb-4">{symbolIndex.symbol_count} symbols indexed</p>
                  <div className="overflow-x-auto max-h-96 overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-card">
                        <tr className="border-b text-left">
                          <th className="pb-2 font-medium text-muted-foreground">Symbol</th>
                          <th className="pb-2 font-medium text-muted-foreground">Kind</th>
                          <th className="pb-2 font-medium text-muted-foreground">Path</th>
                          <th className="pb-2 font-medium text-muted-foreground text-right">Lines</th>
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

function ApiScanPanel() {
  const { data: scan, isLoading } = useApiScanResult();
  const runScan = useRunApiScan();

  const onScan = () => {
    runScan.mutateAsync()
      .then((r) => {
        if (r.status === "completed") {
          toast.success(`Scan complete: ${r.extracted_count} API endpoint(s) from ${r.patterns.length} pattern(s)`);
        } else {
          toast.error(r.error || "API scan failed");
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
            <ScanSearch className="h-4 w-4" /> API Definition Scan
          </CardTitle>
          <CardDescription>
            Use a reasoning model to find where API definitions live across any
            framework/language and generate regexes that extract them. The regexes
            are applied deterministically to the pinned snapshot. Requires a
            configured reasoning model; results are LLM-generated — review before
            trusting.
          </CardDescription>
        </div>
        <Button size="sm" onClick={onScan} disabled={runScan.isPending}>
          <Sparkles className={`h-4 w-4 mr-1 ${runScan.isPending ? "animate-spin" : ""}`} />
          {runScan.isPending ? "Scanning…" : "Scan API definitions"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-10 w-full"/>)}</div>
        ) : !result || result.status === "none" ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            No API scan yet. Create a snapshot, then run a scan to detect APIs in
            frameworks the deterministic indexer does not support.
          </p>
        ) : result.status === "failed" ? (
          <div className="rounded-md border border-red-300 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-4 py-3 text-sm text-red-800 dark:text-red-200">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="h-4 w-4" /> Scan failed
              {result.is_mock && <Badge variant="outline" className="text-[10px]">mock</Badge>}
            </div>
            <p className="mt-1">{result.error}</p>
            <p className="mt-1 text-xs">
              API scanning requires a real reasoning model (set
              INTELLIGENCE_LLM_PROVIDER / INTELLIGENCE_LLM_MODEL, or LLM_PROVIDER /
              LLM_MODEL). No heuristic fallback is used.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge variant="secondary">{result.extracted_count} API endpoint(s)</Badge>
              <span className="text-muted-foreground">
                {result.patterns.length} pattern(s)
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
                    <span className="ml-auto text-xs">{p.match_count} match(es)</span>
                    <span className="text-xs text-muted-foreground">{Math.round(p.confidence * 100)}%</span>
                  </div>
                  <code className="block text-xs font-mono bg-muted/50 rounded px-2 py-1 break-all">{p.regex}</code>
                  <p className="text-xs text-muted-foreground">{p.reason}</p>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              Extracted endpoints appear in the{" "}
              <span className="font-medium">Flow Explorer</span> API list, marked
              as LLM-sourced. They are listed for visibility; a process tree
              cannot yet be rooted from an LLM-only endpoint.
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
      <div className="space-y-2">
        <Label>Repository</Label>
        <Select value={repoPath} onChange={e => setRepoPath(e.target.value)}>
          <option value="">Select repository...</option>
          {config?.repo_path && !candidates.some(candidate => candidate.path === config.repo_path) && (
            <option value={config.repo_path} disabled>
              Unavailable: {config.repo_path}
            </option>
          )}
          {candidates.map(candidate => (
            <option key={candidate.path} value={candidate.path}>
              {candidate.name} — {candidate.path}
            </option>
          ))}
        </Select>
        {!candidates.length && (
          <p className="text-xs text-destructive">
            No Git repositories were found below the configured repository root.
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label>Include Patterns <span className="text-muted-foreground font-normal">(one per line)</span></Label>
        <Textarea value={includePatterns} onChange={e => setIncludePatterns(e.target.value)} placeholder={"*.py\n*.js"} rows={3} />
      </div>
      <div className="space-y-2">
        <Label>Exclude Patterns <span className="text-muted-foreground font-normal">(one per line)</span></Label>
        <Textarea value={excludePatterns} onChange={e => setExcludePatterns(e.target.value)} placeholder={"test_*\n__pycache__"} rows={3} />
      </div>
      <Button onClick={handleSave} disabled={isPending || !repoPath || !candidates.some(candidate => candidate.path === repoPath)}>
        {isPending ? "Saving..." : "Save Configuration"}
      </Button>
    </>
  );
}
