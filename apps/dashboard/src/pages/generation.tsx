import { useState } from "react";
import { Link } from "react-router-dom";
import { useComponents, useTraces, useGenerationRuns, useCreateGenerationRun } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { Sparkles, Bot, ArrowRight } from "lucide-react";

const VERDICT_VARIANT: Record<string, "default" | "success" | "destructive" | "secondary" | "warning"> = {
  better: "success", worse: "destructive", same: "secondary", unsafe: "warning", error: "destructive",
};
const VERDICT_ICON: Record<string, string> = {
  better: "✅", worse: "❌", same: "➖", unsafe: "⚠️", error: "⛔", unknown: "❔",
};

export default function GenerationPage() {
  const { data: components } = useComponents();
  const [selectedComponent, setSelectedComponent] = useState("");
  const [selectedTrace, setSelectedTrace] = useState("");
  const [objective, setObjective] = useState("");
  const { data: traces } = useTraces(selectedComponent || null);
  const { data: runs, isLoading } = useGenerationRuns(selectedComponent || undefined);
  const createRun = useCreateGenerationRun();
  const [expandedRun, setExpandedRun] = useState<number | null>(null);

  const handleGenerate = async () => {
    if (!selectedComponent || !selectedTrace || !objective.trim()) return;
    try {
      await createRun.mutateAsync({
        component_id: selectedComponent,
        trace_id: selectedTrace,
        objective: objective.trim(),
      });
      toast.success("Generation started");
      setObjective("");
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Generate & Evaluate</h1>

      {/* Issue #267 item 10: previously the only pointer to the newer AI
          Candidate Studio was the sidebar "Legacy" badge/tooltip
          (sidebar.tsx) -- add a visible in-page banner too. */}
      <div
        className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-100"
        data-testid="generation-legacy-banner"
      >
        <span>
          このページは旧世代の候補生成機能です。新しい機能開発には
          <span className="font-medium">AI Candidate Studio</span>の利用を推奨します。
        </span>
        <Link
          to="/candidate-studio"
          className="inline-flex items-center gap-1.5 font-medium text-amber-900 hover:underline dark:text-amber-100"
          data-testid="generation-legacy-banner-link"
        >
          <Bot className="h-4 w-4" /> AI Candidate Studioを開く <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">New Generation Run</CardTitle>
          <CardDescription>Generate a candidate implementation using LLM</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>Component</Label>
              <Select value={selectedComponent} onChange={e => { setSelectedComponent(e.target.value); setSelectedTrace(""); }}>
                <option value="">Select component...</option>
                {components?.map(c => <option key={c.component_id} value={c.component_id}>{c.component_id}</option>)}
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Trace</Label>
              <Select value={selectedTrace} onChange={e => setSelectedTrace(e.target.value)} disabled={!selectedComponent}>
                <option value="">Select trace...</option>
                {traces?.map(t => (
                  <option key={t.trace_id} value={t.trace_id}>
                    {t.trace_id.slice(0, 8)} — {formatTimestamp(t.timestamp)}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label>Objective</Label>
            <Textarea value={objective} onChange={e => setObjective(e.target.value)} placeholder="Improve accuracy of..." rows={3} />
          </div>
          <Button
            onClick={handleGenerate}
            disabled={createRun.isPending || !selectedComponent || !selectedTrace || !objective.trim()}
          >
            <Sparkles className="h-4 w-4 mr-1" />
            {createRun.isPending ? "Generating..." : "Generate"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Generation Runs</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">{[1,2,3].map(i => <Skeleton key={i} className="h-16 w-full" />)}</div>
          ) : !runs?.length ? (
            <p className="text-sm text-muted-foreground text-center py-8">No generation runs yet</p>
          ) : (
            <div className="space-y-3">
              {runs.map(run => {
                const expanded = expandedRun === run.id;
                return (
                  <div key={run.id} className="rounded-lg border">
                    <div
                      className="flex items-center justify-between p-4 cursor-pointer"
                      onClick={() => setExpandedRun(expanded ? null : run.id)}
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-2 text-sm font-medium">
                          Run #{run.id} — {run.component_id}
                          {run.llm_verdict && (
                            <Badge variant={VERDICT_VARIANT[run.llm_verdict] ?? "secondary"}>
                              {VERDICT_ICON[run.llm_verdict] ?? ""} {run.llm_verdict}
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">{run.objective}</p>
                      </div>
                      <span className="text-xs text-muted-foreground">{formatTimestamp(run.created_at)}</span>
                    </div>
                    {expanded && (
                      <div className="border-t p-4 space-y-4 text-sm">
                        {run.input_json && (
                          <Section title="Input">
                            <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto">{run.input_json}</pre>
                          </Section>
                        )}
                        <div className="grid gap-4 md:grid-cols-2">
                          {run.current_output && (
                            <Section title="Current Output">
                              <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto">{run.current_output}</pre>
                            </Section>
                          )}
                          {run.candidate_output && (
                            <Section title="Candidate Output">
                              <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto">{run.candidate_output}</pre>
                            </Section>
                          )}
                        </div>
                        {run.generated_code && (
                          <Section title="Generated Code">
                            <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto max-h-64 overflow-y-auto">{run.generated_code}</pre>
                          </Section>
                        )}
                        {run.execution_error && (
                          <Section title="Execution Error">
                            <pre className="rounded-md bg-destructive/10 p-3 text-xs text-destructive overflow-x-auto">{run.execution_error}</pre>
                          </Section>
                        )}
                        {(run.llm_reason || run.llm_risks || run.llm_recommendation) && (
                          <Section title="LLM Analysis">
                            <dl className="space-y-2 text-xs">
                              {run.llm_reason && <div><dt className="font-medium text-muted-foreground">Reason</dt><dd>{run.llm_reason}</dd></div>}
                              {run.llm_risks && <div><dt className="font-medium text-muted-foreground">Risks</dt><dd>{run.llm_risks}</dd></div>}
                              {run.llm_recommendation && <div><dt className="font-medium text-muted-foreground">Recommendation</dt><dd>{run.llm_recommendation}</dd></div>}
                            </dl>
                          </Section>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-medium text-muted-foreground mb-1">{title}</h4>
      {children}
    </div>
  );
}
