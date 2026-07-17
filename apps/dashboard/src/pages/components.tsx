import { Fragment, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  useComponents, useTraces, useUpdatePolicy,
  useComponentProfile, useUpdateComponentProfile,
  useShadowResults, useUpdateEvaluation,
  useCriteria,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Bot } from "lucide-react";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";
import { JsonTree } from "@/components/json-tree";
import { ReplayabilityBadge, ReplayRowActions } from "@/components/replay-row-actions";

const MODES = ["off", "trace", "shadow"] as const;
const EVALUATIONS = ["unknown", "better", "worse", "same"];
const SIGNAL_REFRESH_INTERVAL_MS = 2_000;
const MODE_VARIANT: Record<string, "secondary" | "success" | "warning"> = {
  off: "secondary", trace: "success", shadow: "warning",
};
const PROFILE_FIELD_LABELS_JA: Record<string, string> = {
  purpose: "目的",
  responsibility: "責務",
  expected_input: "想定入力",
  expected_output: "想定出力",
  failure_impact: "失敗時の影響",
};

export default function ComponentsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { data: components, isLoading } = useComponents(SIGNAL_REFRESH_INTERVAL_MS);
  // Deep link from Trace Lineage / analyzer results: /components?component=<id>
  const [selected, setSelected] = useState<string | null>(
    searchParams.get("component"),
  );
  const requestedTraceId = searchParams.get("trace");
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(requestedTraceId);
  const updatePolicy = useUpdatePolicy();
  const { data: traces } = useTraces(
    selected,
    requestedTraceId ? 500 : 20,
    SIGNAL_REFRESH_INTERVAL_MS,
  );
  const { data: profile } = useComponentProfile(selected);
  const updateProfile = useUpdateComponentProfile();
  const { data: shadows } = useShadowResults(selected, 20);
  const updateEval = useUpdateEvaluation();
  const { data: criteria } = useCriteria(selected);

  const current = components?.find(c => c.component_id === selected);
  // Issue #258: `components` rows are created (mode='trace' by default) via
  // GET/PUT .../policy before any traffic ever arrives (see
  // apps/control-server/app/routes/components.py get_policy/put_policy), and
  // /components itself LEFT JOINs traces and COUNTs them -- so a 0-trace
  // component is a real, reachable state, not just a defensive edge case.
  // `current === undefined` (list still loading, or a stale ?component= that
  // no longer resolves) is treated as "unknown", per the escape hatch --
  // only a definitive trace_count === 0 blocks.
  const zeroTraces = current !== undefined && current.trace_count === 0;

  const [profForm, setProfForm] = useState<Record<string, string>>({});
  const profileFields = ["purpose", "responsibility", "expected_input", "expected_output", "failure_impact"] as const;
  const getField = (f: string) => profForm[f] ?? (profile as unknown as Record<string, string>)?.[f] ?? "";

  const saveProfile = async () => {
    if (!selected) return;
    try {
      await updateProfile.mutateAsync({
        component_id: selected,
        purpose: getField("purpose"),
        responsibility: getField("responsibility"),
        expected_input: getField("expected_input"),
        expected_output: getField("expected_output"),
        failure_impact: getField("failure_impact"),
        notes: profile?.notes ?? "",
        created_at: profile?.created_at ?? "",
        updated_at: profile?.updated_at ?? "",
      });
      toast.success("Profileを保存しました");
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      <div className="w-64 shrink-0 overflow-y-auto border rounded-xl p-2 space-y-1">
        <h2 className="px-2 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Components</h2>
        {isLoading ? (
          <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : !components?.length ? (
          <p className="text-xs text-muted-foreground px-2 py-4">componentがありません</p>
        ) : (
          components.map(c => (
            <button
              key={c.component_id}
              className={cn(
                "w-full text-left rounded-lg px-3 py-2 text-sm transition-colors cursor-pointer",
                selected === c.component_id ? "bg-secondary text-foreground" : "hover:bg-secondary/50 text-muted-foreground",
              )}
              onClick={() => { setSelected(c.component_id); setExpandedTraceId(null); setProfForm({}); }}
            >
              <div className="font-mono text-xs truncate">{c.component_id}</div>
              <div className="flex items-center gap-2 mt-1">
                <Badge variant={MODE_VARIANT[c.mode]} className="text-xs">{c.mode}</Badge>
                <span className="text-xs text-muted-foreground">{c.trace_count} traces</span>
              </div>
            </button>
          ))
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            componentを選択すると詳細が表示されます
          </div>
        ) : (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold font-mono">{selected}</h2>
              <div className="flex items-center gap-2">
                <AddToWorkspaceButton itemType="component" itemId={selected} label={selected} />
                <Button
                  size="sm"
                  variant="outline"
                  title={zeroTraces
                    ? "このcomponentのTraceがまだ記録されていません — 候補生成には入出力の実例が必要です。"
                    : "このcomponentのAI Candidate Studioセッションを開始する"}
                  disabled={zeroTraces}
                  onClick={() => navigate(`/candidate-studio?component_id=${encodeURIComponent(selected)}`)}
                >
                  <Bot className="h-4 w-4 mr-1" /> AIで別バージョンを作る
                </Button>
                {MODES.map(m => {
                  // Issue #258 (orchestrator's interpretation of the
                  // components-page gating AC): only the "shadow" mode
                  // switch is gated on trace_count === 0 -- shadow
                  // comparison is meaningless without recorded traffic to
                  // compare a candidate against. "off" <-> "trace" must
                  // NEVER be gated on trace count: switching to "trace" is
                  // precisely how a component starts collecting its first
                  // traces, so gating it here would create a
                  // chicken-and-egg deadlock the escape-hatch AC warns
                  // against.
                  const shadowBlocked = m === "shadow" && zeroTraces;
                  return (
                    <Button
                      key={m}
                      size="sm"
                      variant={current?.mode === m ? "default" : "outline"}
                      onClick={() => updatePolicy.mutateAsync({ componentId: selected, mode: m }).then(() => toast.success(`Mode: ${m}`)).catch(e => toast.error(String(e)))}
                      disabled={updatePolicy.isPending || shadowBlocked}
                      title={shadowBlocked
                        ? "shadow比較には記録済みのTraceが必要です — traceモードに切り替えて収集を開始してください。"
                        : undefined}
                    >
                      {m}
                    </Button>
                  );
                })}
              </div>
            </div>
            {zeroTraces && (
              <p className="text-xs text-muted-foreground" data-testid="component-zero-traces-reason">
                <span className="font-mono">{selected}</span> のTraceはまだ記録されていません —
                AI候補生成とshadow modeには先に実例のトラフィックが必要です。{" "}
                <span className="font-medium">trace</span> モードに切り替えて収集を開始してください。
              </p>
            )}
            {/* Issue #267 item 3: the mode toggle's meaning was previously
                explained only in the Simulation Workbench's EscalationPanel
                (Principle 1's shadow guarantee) -- surface the same
                guarantee here, next to the switch itself. */}
            <p className="text-xs text-muted-foreground" data-testid="component-mode-explanation">
              <span className="font-mono">off</span>=記録なし ·{" "}
              <span className="font-mono">trace</span>=入出力・エラー・durationを記録 ·{" "}
              <span className="font-mono">shadow</span>=候補実装も並行実行して比較記録。
              shadow modeは本番の戻り値を変更しません（Principle 1）。
            </p>

            <Tabs defaultValue="traces">
              <TabsList>
                <TabsTrigger value="traces">Traces</TabsTrigger>
                <TabsTrigger value="shadows">Shadow Results</TabsTrigger>
                <TabsTrigger value="profile">Profile</TabsTrigger>
                <TabsTrigger value="criteria">Criteria</TabsTrigger>
              </TabsList>

              <TabsContent value="traces">
                <Card>
                  <CardContent className="pt-6">
                    {!traces?.length ? (
                      <p className="text-sm text-muted-foreground text-center py-8">Traceがまだありません</p>
                    ) : (
                      <div className="overflow-x-auto max-h-96 overflow-y-auto">
                        <table className="w-full text-sm">
                          <thead className="sticky top-0 bg-card">
                            <tr className="border-b text-left">
                              <th className="pb-2 font-medium text-muted-foreground">Trace ID</th>
                              <th className="pb-2 font-medium text-muted-foreground">Mode</th>
                              <th className="pb-2 font-medium text-muted-foreground">Duration</th>
                              <th className="pb-2 font-medium text-muted-foreground">Status</th>
                              <th className="pb-2 font-medium text-muted-foreground">Replay</th>
                              <th className="pb-2 font-medium text-muted-foreground text-right">Time</th>
                            </tr>
                          </thead>
                          <tbody>
                            {traces.map(t => {
                              const expanded = expandedTraceId === t.trace_id;
                              return (
                                <Fragment key={t.trace_id}>
                                  <tr
                                    className={cn(
                                      "border-b last:border-0",
                                      requestedTraceId === t.trace_id && "bg-amber-50 dark:bg-amber-950/20",
                                    )}
                                  >
                                    <td className="py-2 font-mono text-xs">
                                      <button
                                        type="button"
                                        className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-secondary cursor-pointer"
                                        aria-label={`Trace ${t.trace_id} の詳細を${expanded ? "隠す" : "表示"}`}
                                        aria-expanded={expanded}
                                        onClick={() => setExpandedTraceId(expanded ? null : t.trace_id)}
                                      >
                                        <span className="text-muted-foreground" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
                                        {t.trace_id.slice(0, 12)}
                                      </button>
                                    </td>
                                    <td className="py-2"><Badge variant="outline">{t.mode}</Badge></td>
                                    <td className="py-2 text-xs">{t.duration_ms != null ? `${t.duration_ms}ms` : "—"}</td>
                                    <td className="py-2">
                                      {t.error ? <Badge variant="destructive">error</Badge> : <Badge variant="success">ok</Badge>}
                                    </td>
                                    <td className="py-2">
                                      <ReplayabilityBadge replayability={t.replayability} reasons={t.replay_reasons} />
                                    </td>
                                    <td className="py-2 text-right text-xs text-muted-foreground">{formatTimestamp(t.timestamp)}</td>
                                  </tr>
                                  {expanded && (
                                    <tr key={`${t.trace_id}-details`} className="border-b bg-muted/20">
                                      <td colSpan={6} className="p-4">
                                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                          <span className="text-xs text-muted-foreground">
                                            Trace ID（全体）: <span className="font-mono text-foreground">{t.trace_id}</span>
                                          </span>
                                          <div className="flex items-center gap-1 flex-wrap">
                                            <ReplayRowActions componentId={selected} traceId={t.trace_id} />
                                            <AddToWorkspaceButton
                                              itemType="trace"
                                              itemId={t.trace_id}
                                              label={`Trace ${t.trace_id.slice(0, 12)} (${selected})`}
                                            />
                                          </div>
                                        </div>
                                        <div className="grid gap-4 lg:grid-cols-2">
                                          <div className="min-w-0">
                                            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">入力</h3>
                                            <div className="max-h-64 overflow-auto rounded-md border bg-card p-3">
                                              <JsonTree data={t.input} defaultExpanded />
                                            </div>
                                          </div>
                                          <div className="min-w-0">
                                            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">出力</h3>
                                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-card p-3 font-mono text-xs">{t.output ?? "—"}</pre>
                                          </div>
                                        </div>
                                        {t.error && (
                                          <div className="mt-4">
                                            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-destructive">エラー</h3>
                                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-destructive/40 bg-destructive/5 p-3 font-mono text-xs text-destructive">{t.error}</pre>
                                          </div>
                                        )}
                                      </td>
                                    </tr>
                                  )}
                                </Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="shadows">
                <Card>
                  <CardContent className="pt-6">
                    {!shadows?.length ? (
                      <p className="text-sm text-muted-foreground text-center py-8">Shadow Resultsはまだありません</p>
                    ) : (
                      <div className="space-y-3">
                        {shadows.map(s => (
                          <div key={s.id} className="rounded-lg border p-3 space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="font-mono text-xs">{s.trace_id.slice(0, 12)}</span>
                              <div className="flex items-center gap-2">
                                {EVALUATIONS.map(ev => (
                                  <Button
                                    key={ev}
                                    size="sm"
                                    variant={s.evaluation === ev ? "default" : "ghost"}
                                    className="text-xs h-7 px-2"
                                    onClick={() => updateEval.mutateAsync({ resultId: s.id, evaluation: ev }).catch(e => toast.error(String(e)))}
                                  >
                                    {ev}
                                  </Button>
                                ))}
                              </div>
                            </div>
                            <div className="grid gap-2 md:grid-cols-2 text-xs">
                              <div>
                                <span className="font-medium text-muted-foreground">現在の出力:</span>
                                <pre className="mt-1 rounded bg-muted p-2 overflow-x-auto max-h-24 overflow-y-auto">{s.current_output ?? "—"}</pre>
                              </div>
                              <div>
                                <span className="font-medium text-muted-foreground">候補の出力:</span>
                                <pre className="mt-1 rounded bg-muted p-2 overflow-x-auto max-h-24 overflow-y-auto">{s.candidate_output ?? s.candidate_error ?? "—"}</pre>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="profile">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Component Profile</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {profileFields.map(f => (
                      <div key={f} className="space-y-2">
                        <Label>{PROFILE_FIELD_LABELS_JA[f] ?? f}</Label>
                        <Textarea
                          value={getField(f)}
                          onChange={e => setProfForm(prev => ({ ...prev, [f]: e.target.value }))}
                          rows={2}
                        />
                      </div>
                    ))}
                    <Button onClick={saveProfile} disabled={updateProfile.isPending}>
                      {updateProfile.isPending ? "保存中..." : "Profileを保存"}
                    </Button>
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="criteria">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Evaluation Criteria</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {!criteria?.length ? (
                      <p className="text-sm text-muted-foreground text-center py-8">Criteriaが未定義です</p>
                    ) : (
                      <div className="space-y-2">
                        {criteria.map(c => (
                          <div key={c.id} className="flex items-center justify-between rounded-lg border p-3">
                            <div>
                              <div className="text-sm font-medium">{c.name}</div>
                              <div className="text-xs text-muted-foreground">{c.description}</div>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge variant="outline">{c.criterion_type}</Badge>
                              <Badge variant={c.enabled ? "success" : "secondary"}>
                                {c.enabled ? "enabled" : "disabled"}
                              </Badge>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>
        )}
      </div>
    </div>
  );
}
