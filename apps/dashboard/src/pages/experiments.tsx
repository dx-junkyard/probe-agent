import { useState } from "react";
import {
  useExperiments, useRunExperiment, useExperimentDecision,
  useCreateExperiment, useSnapshots, useLatestDrafts,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { Play, Download, Plus, Trash2 } from "lucide-react";
import type { ExperimentOut } from "@/api/types";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";

const STATUS_VARIANT: Record<string, "default" | "success" | "destructive" | "secondary" | "warning"> = {
  draft: "secondary",
  running: "warning",
  completed: "success",
  failed: "destructive",
};

const DECISION_OPTS = ["undecided", "adopted", "rejected", "needs_more_data"];

interface VariantInput {
  label: string;
  patch_text: string;
  risk_note: string;
}

export default function ExperimentsPage() {
  const { data: experiments, isLoading } = useExperiments();
  const runExperiment = useRunExperiment();
  const makeDecision = useExperimentDecision();
  const createExperiment = useCreateExperiment();
  const { data: snapshots } = useSnapshots();
  const { data: drafts } = useLatestDrafts();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const [newFeatureId, setNewFeatureId] = useState("");
  const [newObjective, setNewObjective] = useState("");
  const [newSnapshotId, setNewSnapshotId] = useState<string>("");
  const [variants, setVariants] = useState<VariantInput[]>([
    { label: "", patch_text: "", risk_note: "" },
    { label: "", patch_text: "", risk_note: "" },
  ]);

  const readySnapshots = snapshots?.filter(s => s.status === "ready") ?? [];
  const features = drafts?.feature_drafts ?? [];

  const resetForm = () => {
    setNewFeatureId("");
    setNewObjective("");
    setNewSnapshotId("");
    setVariants([
      { label: "", patch_text: "", risk_note: "" },
      { label: "", patch_text: "", risk_note: "" },
    ]);
  };

  const handleCreate = async () => {
    if (!newFeatureId || !newObjective.trim() || !newSnapshotId) return;
    const validVariants = variants.filter(v => v.label.trim() && v.patch_text.trim());
    if (validVariants.length < 2) {
      toast.error("At least two variants with label and patch are required");
      return;
    }
    try {
      await createExperiment.mutateAsync({
        feature_id: newFeatureId,
        objective: newObjective.trim(),
        snapshot_id: Number(newSnapshotId),
        variants: validVariants.map(v => ({
          label: v.label.trim(),
          patch_text: v.patch_text,
          risk_note: v.risk_note.trim() || undefined,
        })),
      });
      toast.success("Experiment created");
      setShowCreate(false);
      resetForm();
    } catch (err) { toast.error(String(err)); }
  };

  const updateVariant = (idx: number, field: keyof VariantInput, value: string) => {
    setVariants(prev => prev.map((v, i) => i === idx ? { ...v, [field]: value } : v));
  };

  const addVariant = () => {
    setVariants(prev => [...prev, { label: "", patch_text: "", risk_note: "" }]);
  };

  const removeVariant = (idx: number) => {
    if (variants.length <= 2) return;
    setVariants(prev => prev.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Experiments</h1>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-1" />
          New Experiment
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-4">{[1,2].map(i => <Skeleton key={i} className="h-40 w-full" />)}</div>
      ) : !experiments?.length ? (
        <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No experiments yet. Create one to get started.</CardContent></Card>
      ) : (
        <div className="space-y-4">
          {experiments.map(exp => (
            <ExperimentCard
              key={exp.id}
              exp={exp}
              expanded={expandedId === exp.id}
              onToggle={() => setExpandedId(expandedId === exp.id ? null : exp.id)}
              runExperiment={runExperiment}
              makeDecision={makeDecision}
            />
          ))}
        </div>
      )}

      <Dialog open={showCreate} onOpenChange={(open) => { setShowCreate(open); if (!open) resetForm(); }}>
        <DialogHeader>
          <DialogTitle>Create Experiment</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 max-h-[60vh] overflow-y-auto">
          <div className="space-y-2">
            <Label>Feature</Label>
            {features.length > 0 ? (
              <Select value={newFeatureId} onChange={e => setNewFeatureId(e.target.value)}>
                <option value="">Select feature...</option>
                {features.map(f => <option key={f.feature_id} value={f.feature_id}>{f.feature_id} — {f.name}</option>)}
              </Select>
            ) : (
              <Input value={newFeatureId} onChange={e => setNewFeatureId(e.target.value)} placeholder="feature-id" />
            )}
          </div>
          <div className="space-y-2">
            <Label>Objective</Label>
            <Textarea value={newObjective} onChange={e => setNewObjective(e.target.value)} placeholder="What are you trying to learn?" rows={2} />
          </div>
          <div className="space-y-2">
            <Label>Snapshot</Label>
            <Select value={newSnapshotId} onChange={e => setNewSnapshotId(e.target.value)}>
              <option value="">Select snapshot...</option>
              {readySnapshots.map(s => (
                <option key={s.id} value={s.id}>
                  #{s.id} — {s.commit_sha?.slice(0, 8)} ({s.file_count} files)
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>Variants</Label>
              <Button variant="ghost" size="sm" onClick={addVariant}>
                <Plus className="h-3 w-3 mr-1" /> Add
              </Button>
            </div>
            {variants.map((v, i) => (
              <div key={i} className="rounded-lg border p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">Variant {i + 1}</span>
                  {variants.length > 2 && (
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => removeVariant(i)}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  )}
                </div>
                <Input
                  placeholder="Label (e.g., optimized-v1)"
                  value={v.label}
                  onChange={e => updateVariant(i, "label", e.target.value)}
                />
                <Textarea
                  placeholder="Patch text (unified diff format)"
                  value={v.patch_text}
                  onChange={e => updateVariant(i, "patch_text", e.target.value)}
                  rows={4}
                  className="font-mono text-xs"
                />
                <Input
                  placeholder="Risk note (optional)"
                  value={v.risk_note}
                  onChange={e => updateVariant(i, "risk_note", e.target.value)}
                />
              </div>
            ))}
          </div>
          <Button
            onClick={handleCreate}
            disabled={createExperiment.isPending || !newFeatureId || !newObjective.trim() || !newSnapshotId || variants.filter(v => v.label.trim() && v.patch_text.trim()).length < 2}
            className="w-full"
          >
            {createExperiment.isPending ? "Creating..." : "Create Experiment"}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function ExperimentCard({ exp, expanded, onToggle, runExperiment, makeDecision }: {
  exp: ExperimentOut;
  expanded: boolean;
  onToggle: () => void;
  runExperiment: ReturnType<typeof useRunExperiment>;
  makeDecision: ReturnType<typeof useExperimentDecision>;
}) {
  const [decisionVal, setDecisionVal] = useState(exp.human_decision ?? "undecided");
  const [decisionVariant, setDecisionVariant] = useState(exp.human_decision_variant_key ?? "");
  const [decisionNote, setDecisionNote] = useState(exp.human_decision_note ?? "");

  const nonBaselineVariants = exp.variants?.filter(v => !v.is_baseline && v.status === "completed") ?? [];

  const handleDecision = async () => {
    const payload: { id: number; decision: string; variant_key?: string; note?: string } = {
      id: exp.id,
      decision: decisionVal,
    };
    if (decisionVal === "adopted") {
      if (!decisionVariant) {
        toast.error("Select a variant to adopt");
        return;
      }
      if (!decisionNote.trim()) {
        toast.error("A note is required for adoption");
        return;
      }
      payload.variant_key = decisionVariant;
    }
    if (decisionNote.trim()) {
      payload.note = decisionNote.trim();
    }
    try {
      await makeDecision.mutateAsync(payload);
      toast.success("Decision saved");
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={onToggle}>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle className="text-sm flex items-center gap-2">
              Experiment #{exp.id}
              <Badge variant={STATUS_VARIANT[exp.status] ?? "secondary"}>{exp.status}</Badge>
              {exp.human_decision && exp.human_decision !== "undecided" && (
                <Badge variant="outline">{exp.human_decision}</Badge>
              )}
            </CardTitle>
            <CardDescription className="mt-1">
              {exp.feature_id} — {exp.objective}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-muted-foreground">{formatTimestamp(exp.created_at)}</span>
            <div onClick={e => e.stopPropagation()}>
              <AddToWorkspaceButton itemType="experiment" itemId={String(exp.id)} label={`Experiment #${exp.id}: ${exp.feature_id}`} />
            </div>
          </div>
        </div>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            {exp.status === "draft" && (
              <Button
                size="sm"
                onClick={() => runExperiment.mutateAsync(exp.id).then(() => toast.success("Experiment started")).catch(e => toast.error(String(e)))}
                disabled={runExperiment.isPending}
              >
                <Play className="h-4 w-4 mr-1" />
                Run Experiment
              </Button>
            )}
          </div>

          {exp.status === "completed" && (
            <div className="rounded-lg border p-4 space-y-3">
              <h4 className="text-sm font-medium">Decision</h4>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Verdict</Label>
                  <Select value={decisionVal} onChange={e => setDecisionVal(e.target.value)}>
                    {DECISION_OPTS.map(d => <option key={d} value={d}>{d}</option>)}
                  </Select>
                </div>
                {decisionVal === "adopted" && (
                  <div className="space-y-2">
                    <Label>Adopt Variant *</Label>
                    <Select value={decisionVariant} onChange={e => setDecisionVariant(e.target.value)}>
                      <option value="">Select variant...</option>
                      {nonBaselineVariants.map(v => (
                        <option key={v.variant_key} value={v.variant_key}>{v.label} ({v.variant_key})</option>
                      ))}
                    </Select>
                  </div>
                )}
              </div>
              <div className="space-y-2">
                <Label>Note {decisionVal === "adopted" ? "*" : ""}</Label>
                <Textarea
                  value={decisionNote}
                  onChange={e => setDecisionNote(e.target.value)}
                  placeholder="Reason for decision..."
                  rows={2}
                />
              </div>
              <Button size="sm" onClick={handleDecision} disabled={makeDecision.isPending}>
                {makeDecision.isPending ? "Saving..." : "Save Decision"}
              </Button>
            </div>
          )}

          {exp.variants?.length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">Variants</h4>
              <div className="space-y-3">
                {exp.variants.map(v => (
                  <div key={v.id} className="rounded-lg border p-3">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{v.label}</span>
                        <span className="text-xs text-muted-foreground font-mono">{v.variant_key}</span>
                        {v.is_baseline && <Badge variant="outline">baseline</Badge>}
                        <Badge variant={v.status === "completed" ? "success" : v.status === "failed" ? "destructive" : "secondary"}>
                          {v.status}
                        </Badge>
                      </div>
                      {v.patch_text && (
                        <Button
                          size="sm" variant="ghost"
                          onClick={() => {
                            const blob = new Blob([v.patch_text!], { type: "text/plain" });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a"); a.href = url; a.download = `variant-${v.variant_key}.diff`; a.click();
                            URL.revokeObjectURL(url);
                          }}
                        >
                          <Download className="h-3 w-3 mr-1" /> Patch
                        </Button>
                      )}
                    </div>
                    {v.risk_note && <p className="text-xs text-muted-foreground mb-1">{v.risk_note}</p>}
                    {v.error && <p className="text-xs text-destructive mb-2">{v.error}</p>}
                    {v.metrics && Object.keys(v.metrics).length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b">
                              <th className="pb-1 text-left font-medium text-muted-foreground">Metric</th>
                              <th className="pb-1 text-right font-medium text-muted-foreground">Value</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(v.metrics).map(([k, val]) => (
                              <tr key={k} className="border-b last:border-0">
                                <td className="py-1">{k}</td>
                                <td className="py-1 text-right font-mono">{String(val)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {exp.comparison && Object.keys(exp.comparison).length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">Comparison</h4>
              <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                {JSON.stringify(exp.comparison, null, 2)}
              </pre>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
