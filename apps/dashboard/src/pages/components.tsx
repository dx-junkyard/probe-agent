import { useState } from "react";
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
import { AddToWorkspaceButton } from "@/components/add-to-workspace";

const MODES = ["off", "trace", "shadow"] as const;
const EVALUATIONS = ["unknown", "better", "worse", "same"];
const MODE_VARIANT: Record<string, "secondary" | "success" | "warning"> = {
  off: "secondary", trace: "success", shadow: "warning",
};

export default function ComponentsPage() {
  const { data: components, isLoading } = useComponents();
  const [selected, setSelected] = useState<string | null>(null);
  const updatePolicy = useUpdatePolicy();
  const { data: traces } = useTraces(selected, 20);
  const { data: profile } = useComponentProfile(selected);
  const updateProfile = useUpdateComponentProfile();
  const { data: shadows } = useShadowResults(selected, 20);
  const updateEval = useUpdateEvaluation();
  const { data: criteria } = useCriteria(selected);

  const current = components?.find(c => c.component_id === selected);

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
      toast.success("Profile saved");
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      <div className="w-64 shrink-0 overflow-y-auto border rounded-xl p-2 space-y-1">
        <h2 className="px-2 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Components</h2>
        {isLoading ? (
          <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : !components?.length ? (
          <p className="text-xs text-muted-foreground px-2 py-4">No components</p>
        ) : (
          components.map(c => (
            <button
              key={c.component_id}
              className={cn(
                "w-full text-left rounded-lg px-3 py-2 text-sm transition-colors cursor-pointer",
                selected === c.component_id ? "bg-secondary text-foreground" : "hover:bg-secondary/50 text-muted-foreground",
              )}
              onClick={() => { setSelected(c.component_id); setProfForm({}); }}
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
            Select a component to view details
          </div>
        ) : (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold font-mono">{selected}</h2>
              <div className="flex items-center gap-2">
                <AddToWorkspaceButton itemType="component" itemId={selected} label={selected} />
                {MODES.map(m => (
                  <Button
                    key={m}
                    size="sm"
                    variant={current?.mode === m ? "default" : "outline"}
                    onClick={() => updatePolicy.mutateAsync({ componentId: selected, mode: m }).then(() => toast.success(`Mode: ${m}`)).catch(e => toast.error(String(e)))}
                    disabled={updatePolicy.isPending}
                  >
                    {m}
                  </Button>
                ))}
              </div>
            </div>

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
                      <p className="text-sm text-muted-foreground text-center py-8">No traces yet</p>
                    ) : (
                      <div className="overflow-x-auto max-h-96 overflow-y-auto">
                        <table className="w-full text-sm">
                          <thead className="sticky top-0 bg-card">
                            <tr className="border-b text-left">
                              <th className="pb-2 font-medium text-muted-foreground">Trace ID</th>
                              <th className="pb-2 font-medium text-muted-foreground">Mode</th>
                              <th className="pb-2 font-medium text-muted-foreground">Duration</th>
                              <th className="pb-2 font-medium text-muted-foreground">Status</th>
                              <th className="pb-2 font-medium text-muted-foreground text-right">Time</th>
                            </tr>
                          </thead>
                          <tbody>
                            {traces.map(t => (
                              <tr key={t.trace_id} className="border-b last:border-0">
                                <td className="py-2 font-mono text-xs">{t.trace_id.slice(0, 12)}</td>
                                <td className="py-2"><Badge variant="outline">{t.mode}</Badge></td>
                                <td className="py-2 text-xs">{t.duration_ms != null ? `${t.duration_ms}ms` : "—"}</td>
                                <td className="py-2">
                                  {t.error ? <Badge variant="destructive">error</Badge> : <Badge variant="success">ok</Badge>}
                                </td>
                                <td className="py-2 text-right text-xs text-muted-foreground">{formatTimestamp(t.timestamp)}</td>
                              </tr>
                            ))}
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
                      <p className="text-sm text-muted-foreground text-center py-8">No shadow results yet</p>
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
                                <span className="font-medium text-muted-foreground">Current:</span>
                                <pre className="mt-1 rounded bg-muted p-2 overflow-x-auto max-h-24 overflow-y-auto">{s.current_output ?? "—"}</pre>
                              </div>
                              <div>
                                <span className="font-medium text-muted-foreground">Candidate:</span>
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
                        <Label className="capitalize">{f.replace("_", " ")}</Label>
                        <Textarea
                          value={getField(f)}
                          onChange={e => setProfForm(prev => ({ ...prev, [f]: e.target.value }))}
                          rows={2}
                        />
                      </div>
                    ))}
                    <Button onClick={saveProfile} disabled={updateProfile.isPending}>
                      {updateProfile.isPending ? "Saving..." : "Save Profile"}
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
                      <p className="text-sm text-muted-foreground text-center py-8">No criteria defined</p>
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
