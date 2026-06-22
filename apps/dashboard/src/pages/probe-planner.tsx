import {
  useProbePlans, useGenerateProbePlan, useUpdateProbePointStatus,
  useProbePatches, useGeneratePatch, useValidatePatch, useApplyProbePatch,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { Crosshair, CheckCircle, XCircle, FileCode, Play, Download, GitBranch } from "lucide-react";
import { useState } from "react";
import type { ProbePatchOut } from "@/api/types";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";

export default function ProbePlannerPage() {
  const { data: plansData, isLoading } = useProbePlans();
  const generatePlan = useGenerateProbePlan();
  const updatePointStatus = useUpdateProbePointStatus();
  const { data: patches } = useProbePatches();
  const generatePatch = useGeneratePatch();
  const validatePatch = useValidatePatch();
  const applyPatch = useApplyProbePatch();
  const [expandedPlan, setExpandedPlan] = useState<number | null>(null);
  const [applyTarget, setApplyTarget] = useState<ProbePatchOut | null>(null);
  const [applyConfirmation, setApplyConfirmation] = useState("");

  const plans = plansData?.plans ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Probe Planner</h1>
        <Button
          size="sm"
          onClick={() => generatePlan.mutateAsync().then(() => toast.success("Plan generated")).catch(e => toast.error(String(e)))}
          disabled={generatePlan.isPending}
        >
          <Crosshair className="h-4 w-4 mr-1" />
          {generatePlan.isPending ? "Generating..." : "Generate Plan"}
        </Button>
      </div>

      {plansData?.is_mock && (
        <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          Mock data — not from a real LLM analysis.
        </div>
      )}

      {isLoading ? (
        <div className="space-y-4">{[1,2].map(i => <Skeleton key={i} className="h-40 w-full" />)}</div>
      ) : !plans.length ? (
        <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No probe plans yet. Generate a plan to start.</CardContent></Card>
      ) : (
        <div className="space-y-4">
          {plans.map(plan => {
            const expanded = expandedPlan === plan.id;
            const planPatches = patches?.filter(p => p.plan_id === plan.id) ?? [];
            return (
              <Card key={plan.id}>
                <CardHeader
                  className="cursor-pointer"
                  onClick={() => setExpandedPlan(expanded ? null : plan.id)}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <CardTitle className="text-sm flex items-center gap-2">
                        Feature: {plan.feature_id}
                        <Badge variant={plan.status === "approved" ? "success" : plan.status === "rejected" ? "destructive" : "secondary"}>
                          {plan.status}
                        </Badge>
                      </CardTitle>
                      <CardDescription className="mt-1">{plan.objective}</CardDescription>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-xs text-muted-foreground">{formatTimestamp(plan.created_at)}</span>
                      <div onClick={e => e.stopPropagation()}>
                        <AddToWorkspaceButton itemType="probe_plan" itemId={String(plan.id)} label={`Plan: ${plan.feature_id}`} />
                      </div>
                    </div>
                  </div>
                </CardHeader>
                {expanded && (
                  <CardContent className="space-y-4">
                    <div>
                      <h4 className="text-sm font-medium mb-2">Probe Points ({plan.probe_points.length})</h4>
                      <div className="space-y-2">
                        {plan.probe_points.map(pt => (
                          <div key={pt.id} className="flex items-center justify-between rounded-lg border p-3 text-sm">
                            <div className="space-y-1">
                              <div className="font-mono text-xs">{pt.path}:{pt.symbol}</div>
                              <div className="text-xs text-muted-foreground">
                                Lines {pt.line_start}–{pt.line_end} · Mode: {pt.recommended_mode} · Risk: {pt.side_effect_risk}
                              </div>
                              <div className="text-xs">{pt.reason}</div>
                            </div>
                            <div className="flex items-center gap-1 shrink-0 ml-4">
                              <Badge variant={pt.status === "approved" ? "success" : pt.status === "rejected" ? "destructive" : "secondary"}>
                                {pt.status}
                              </Badge>
                              <Button
                                variant="ghost" size="icon" className="h-7 w-7"
                                onClick={() => updatePointStatus.mutateAsync({ pointId: pt.id, status: "approved" }).catch(e => toast.error(String(e)))}
                              >
                                <CheckCircle className="h-4 w-4 text-emerald-600" />
                              </Button>
                              <Button
                                variant="ghost" size="icon" className="h-7 w-7"
                                onClick={() => updatePointStatus.mutateAsync({ pointId: pt.id, status: "rejected" }).catch(e => toast.error(String(e)))}
                              >
                                <XCircle className="h-4 w-4 text-red-500" />
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="flex gap-2">
                      <Button
                        size="sm" variant="outline"
                        onClick={() => generatePatch.mutateAsync(plan.id).then(() => toast.success("Patch generated")).catch(e => toast.error(String(e)))}
                        disabled={generatePatch.isPending}
                      >
                        <FileCode className="h-4 w-4 mr-1" />
                        Generate Patch
                      </Button>
                    </div>

                    {planPatches.length > 0 && (
                      <div>
                        <h4 className="text-sm font-medium mb-2">Patches</h4>
                        {planPatches.map(patch => (
                          <div key={patch.id} className="rounded-lg border p-3 space-y-2">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <Badge variant={patch.apply_status === "applied" ? "success" : patch.status === "failed" ? "destructive" : "secondary"}>
                                  {patch.apply_status === "applied" ? "applied" : patch.status}
                                </Badge>
                                <span className="font-mono text-xs">{patch.commit_sha?.slice(0, 8)}</span>
                              </div>
                              <div className="flex gap-1">
                                <Button
                                  size="sm" variant="outline"
                                  onClick={() => validatePatch.mutateAsync(patch.id).then(() => toast.success("Validation started")).catch(e => toast.error(String(e)))}
                                  disabled={validatePatch.isPending}
                                >
                                  <Play className="h-3 w-3 mr-1" />
                                  Validate
                                </Button>
                                {patch.apply_status !== "applied" && (
                                  <Button
                                    size="sm"
                                    variant="destructive"
                                    onClick={() => {
                                      setApplyTarget(patch);
                                      setApplyConfirmation("");
                                    }}
                                    disabled={applyPatch.isPending}
                                  >
                                    <GitBranch className="h-3 w-3 mr-1" />
                                    Apply
                                  </Button>
                                )}
                                {patch.diff && (
                                  <Button
                                    size="sm" variant="ghost"
                                    onClick={() => {
                                      const blob = new Blob([patch.diff], { type: "text/plain" });
                                      const url = URL.createObjectURL(blob);
                                      const a = document.createElement("a"); a.href = url; a.download = `patch-${patch.id}.diff`; a.click();
                                      URL.revokeObjectURL(url);
                                    }}
                                  >
                                    <Download className="h-3 w-3 mr-1" />
                                    Download
                                  </Button>
                                )}
                              </div>
                            </div>
                            {patch.diff && (
                              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs font-mono max-h-64 overflow-y-auto">
                                {patch.diff}
                              </pre>
                            )}
                            {patch.apply_status === "applied" && (
                              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-200">
                                Applied to the repository working tree. Review and commit the changes before creating a new snapshot.
                              </div>
                            )}
                            {patch.apply_error && (
                              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-200">
                                Apply failed: {patch.apply_error}
                              </div>
                            )}
                            {patch.validation_runs?.length > 0 && (
                              <div className="space-y-1">
                                <h5 className="text-xs font-medium">Validation Runs</h5>
                                {patch.validation_runs.map(vr => (
                                  <div key={vr.id} className="text-xs flex items-center gap-2">
                                    <Badge variant={vr.overall_success ? "success" : "destructive"} className="text-xs">
                                      {vr.overall_success ? "PASS" : "FAIL"}
                                    </Badge>
                                    <span>{vr.variant}</span>
                                    {vr.total_duration_ms && <span className="text-muted-foreground">{vr.total_duration_ms}ms</span>}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                )}
              </Card>
            );
          })}
        </div>
      )}

      <Dialog
        open={applyTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setApplyTarget(null);
            setApplyConfirmation("");
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Apply Probe Patch to Repository</DialogTitle>
        </DialogHeader>
        {applyTarget && (
          <div className="space-y-4">
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-100">
              This writes the instrumentation patch directly to the source repository working tree. It does not create a commit.
            </div>
            <div className="space-y-1 text-sm">
              <div>Snapshot commit: <code className="font-mono text-xs">{applyTarget.commit_sha}</code></div>
              <div>The repository HEAD must match this commit and the working tree must be clean.</div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Type APPLY to confirm</label>
              <Input
                value={applyConfirmation}
                onChange={e => setApplyConfirmation(e.target.value)}
                placeholder="APPLY"
              />
            </div>
            <Button
              variant="destructive"
              className="w-full"
              disabled={applyConfirmation !== "APPLY" || applyPatch.isPending}
              onClick={() => {
                applyPatch.mutateAsync({
                  patchId: applyTarget.id,
                  expectedCommitSha: applyTarget.commit_sha,
                }).then(() => {
                  toast.success("Patch applied to repository");
                  setApplyTarget(null);
                  setApplyConfirmation("");
                }).catch(e => toast.error(String(e)));
              }}
            >
              {applyPatch.isPending ? "Applying..." : "Apply to Repository"}
            </Button>
          </div>
        )}
      </Dialog>
    </div>
  );
}
