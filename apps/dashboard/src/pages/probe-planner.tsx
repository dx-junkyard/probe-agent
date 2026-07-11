import {
  useProbePlans, useGenerateProbePlan, useUpdateProbePointStatus,
  useProbePatches, useGeneratePatch, useValidatePatch, useApplyProbePatch,
  useLatestDrafts, useWorkspaceProposalDraft, useRepositoryStatus,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { Crosshair, CheckCircle, XCircle, FileCode, Play, Download, GitBranch, FlaskConical } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ProbePatchOut } from "@/api/types";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";
import { ContextHeader } from "@/components/layout/context-header";

export default function ProbePlannerPage() {
  const [searchParams] = useSearchParams();
  const draftIdParam = searchParams.get("draft");
  const workspaceIdParam = searchParams.get("workspace");
  const planIdParam = searchParams.get("plan");
  const draftId = draftIdParam && Number.isInteger(Number(draftIdParam)) ? Number(draftIdParam) : null;
  const planId = planIdParam && Number.isInteger(Number(planIdParam)) ? Number(planIdParam) : null;
  // Issue #176: carried from Flow Explorer when the plan was created there
  // from a capability context, so the developer can navigate back to it.
  const capabilityContext = searchParams.get("capability");
  const { data: workspaceDraft } = useWorkspaceProposalDraft(draftId);
  const { data: featureDrafts } = useLatestDrafts();
  const { data: plansData, isLoading } = useProbePlans();
  const generatePlan = useGenerateProbePlan();
  const updatePointStatus = useUpdateProbePointStatus();
  const { data: patches } = useProbePatches();
  const { data: repoStatus } = useRepositoryStatus();
  const generatePatch = useGeneratePatch();
  const validatePatch = useValidatePatch();
  const applyPatch = useApplyProbePatch();
  // undefined = no manual selection yet, so a `?plan=` param can still drive it.
  const [userExpandedPlan, setUserExpandedPlan] = useState<number | null | undefined>(undefined);
  const [applyTarget, setApplyTarget] = useState<ProbePatchOut | null>(null);
  const [applyConfirmation, setApplyConfirmation] = useState("");
  const [showGenerate, setShowGenerate] = useState(false);
  const [draftDismissed, setDraftDismissed] = useState(false);
  const [featureId, setFeatureId] = useState<string | null>(null);
  const [objective, setObjective] = useState<string | null>(null);
  // Issue #212: when no Feature Map drafts exist, free-text feature id entry
  // is an explicit escape hatch the developer must opt into, not the default
  // control — otherwise a plan can be generated unconnected to any
  // capability/purpose. Deterministic presence check only (drafts.length).
  const [manualFeatureEntry, setManualFeatureEntry] = useState(false);
  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const planParamNotified = useRef(false);

  const plans = plansData?.plans ?? [];
  const planMatch = planId !== null && plans.some(plan => plan.id === planId);
  const expandedPlan = userExpandedPlan !== undefined ? userExpandedPlan : (planMatch ? planId : null);
  const setExpandedPlan = setUserExpandedPlan;

  useEffect(() => {
    if (planParamNotified.current || planId === null || isLoading) return;
    planParamNotified.current = true;
    if (planMatch) {
      cardRefs.current[planId]?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    } else {
      toast.error(`Probe plan #${planId} was not found.`);
    }
  }, [planId, isLoading, planMatch]);

  const features = featureDrafts?.feature_drafts ?? [];
  const formFeatureId = featureId
    ?? (workspaceDraft?.draft_type === "probe_plan_draft" ? workspaceDraft.payload.feature_id ?? "" : "");
  const formObjective = objective
    ?? (workspaceDraft?.draft_type === "probe_plan_draft" ? workspaceDraft.payload.objective ?? "" : "");
  const draftOpen = !!workspaceDraft
    && workspaceDraft.draft_type === "probe_plan_draft"
    && !draftDismissed;
  // A Decision Workspace draft carries an already-decided feature id, not a
  // free-text guess, so it is shown even before the developer opts into
  // manual entry.
  const hasWorkspaceFeaturePrefill = workspaceDraft?.draft_type === "probe_plan_draft"
    && !!workspaceDraft.payload.feature_id;
  const showManualFeatureInput = manualFeatureEntry || hasWorkspaceFeaturePrefill;

  const generate = async () => {
    if (!formFeatureId.trim()) return;
    try {
      await generatePlan.mutateAsync({
        featureId: formFeatureId.trim(),
        objective: formObjective.trim() || undefined,
      });
      toast.success("Plan generated");
      setShowGenerate(false);
      setDraftDismissed(true);
      setFeatureId(null);
      setObjective(null);
      setManualFeatureEntry(false);
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="space-y-6">
      <ContextHeader />
      {capabilityContext && (
        <Link
          to={`/capability-map?capability=${encodeURIComponent(capabilityContext)}`}
          className="inline-flex items-center text-xs text-primary hover:underline"
          data-testid="back-to-capability"
        >
          ← Back to Capability: {capabilityContext}
        </Link>
      )}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Probe Planner</h1>
        <Button
          size="sm"
          onClick={() => {
            setDraftDismissed(true);
            setFeatureId("");
            setObjective("");
            setManualFeatureEntry(false);
            setShowGenerate(true);
          }}
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
              <Card key={plan.id} ref={el => { cardRefs.current[plan.id] = el; }}>
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
                        {plan.origin && plan.origin !== "manual" && (
                          <Badge variant="outline" className="text-[10px]">
                            {plan.origin.replace("_", " ")}
                          </Badge>
                        )}
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
                      {capabilityContext && (
                        <Link
                          to={`/experiments?capability=${encodeURIComponent(capabilityContext)}`}
                          className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-input bg-background px-3 text-xs font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
                          data-testid="open-experiments-with-capability"
                        >
                          <FlaskConical className="h-4 w-4" />
                          Experiments
                        </Link>
                      )}
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
                                <span className="font-mono text-xs" title="Generated for this commit">
                                  {patch.commit_sha?.slice(0, 8)}
                                </span>
                                {repoStatus?.current_head
                                  && repoStatus.current_head !== patch.commit_sha
                                  && patch.apply_status !== "applied" && (
                                  <Badge variant="destructive" data-testid="patch-stale-badge">HEAD changed</Badge>
                                )}
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
                              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-200 space-y-1">
                                <div className="font-medium">Applied to the repository working tree (no commit created).</div>
                                <ol className="list-decimal pl-4 space-y-0.5">
                                  <li>Review the patched files and run tests.</li>
                                  <li>Commit the changes in the repository.</li>
                                  <li>
                                    Create a new snapshot and rebuild analysis from the{" "}
                                    <Link to="/repository" className="underline">Repository Refresh Hub</Link>.
                                  </li>
                                </ol>
                              </div>
                            )}
                            <PatchRecovery patch={patch} currentHead={repoStatus?.current_head ?? null} />
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
        open={showGenerate || draftOpen}
        onOpenChange={(open) => {
          setShowGenerate(open);
          if (!open) {
            setDraftDismissed(true);
            setFeatureId(null);
            setObjective(null);
            setManualFeatureEntry(false);
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Generate Probe Plan</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {workspaceDraft?.draft_type === "probe_plan_draft" && (
            <div className="rounded-md border bg-secondary/30 px-3 py-2 text-xs">
              Prefilled from Decision Workspace proposal #{workspaceDraft.proposal_id}.
              {(workspaceDraft.payload.constraints?.length ?? 0) > 0 && (
                <span className="ml-2">Constraints: {workspaceDraft.payload.constraints?.join(", ")}.</span>
              )}
              {(workspaceDraft.payload.evaluation_criteria?.length ?? 0) > 0 && (
                <span className="ml-2">Evaluation: {workspaceDraft.payload.evaluation_criteria?.join(", ")}.</span>
              )}
              {workspaceDraft.missing_fields.length > 0 && (
                <span className="ml-1 text-muted-foreground">
                  Missing prerequisites: {workspaceDraft.missing_fields.join(", ")}.
                </span>
              )}
              {workspaceIdParam && (
                <Link className="ml-2 underline" to={`/workspaces?open=${workspaceIdParam}`}>
                  Back to workspace
                </Link>
              )}
            </div>
          )}
          <div className="space-y-2">
            <Label>Feature</Label>
            {features.length > 0 ? (
              <Select value={formFeatureId} onChange={e => setFeatureId(e.target.value)}>
                <option value="">Select feature...</option>
                {features.map(feature => (
                  <option key={feature.feature_id} value={feature.feature_id}>
                    {feature.feature_id} — {feature.name}
                  </option>
                ))}
              </Select>
            ) : (
              <div className="space-y-2" data-testid="planner-no-drafts-note">
                <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
                  No Feature Map drafts yet, so there is nothing to select a feature from. Generate
                  drafts on the{" "}
                  <Link to="/feature-map" className="underline">Feature Map</Link> page — build{" "}
                  <Link to="/system-understanding" className="underline">System Understanding</Link>{" "}
                  first if no snapshot or analysis exists yet.
                </div>
                {showManualFeatureInput ? (
                  <Input
                    value={formFeatureId}
                    onChange={e => setFeatureId(e.target.value)}
                    placeholder="feature-id"
                    data-testid="planner-manual-feature-input"
                  />
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="text-xs"
                    onClick={() => setManualFeatureEntry(true)}
                    data-testid="planner-manual-feature-toggle"
                  >
                    Enter feature id manually (advanced)
                  </Button>
                )}
              </div>
            )}
          </div>
          <div className="space-y-2">
            <Label>Observation objective</Label>
            <Textarea
              value={formObjective}
              onChange={e => setObjective(e.target.value)}
              placeholder="What should this probe plan help determine?"
              rows={3}
            />
          </div>
          <Button className="w-full" onClick={generate} disabled={!formFeatureId.trim() || generatePlan.isPending}>
            {generatePlan.isPending ? "Generating..." : "Generate Plan"}
          </Button>
        </div>
      </Dialog>

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

// Patch apply recovery guidance (Issue #158). When HEAD moved past the commit a
// patch was generated for, or an apply failed, show the safe read-only commands
// and next actions instead of leaving the user stuck. Nothing here runs git —
// destructive/remote operations stay manual and explicit.
function PatchRecovery({ patch, currentHead }: { patch: ProbePatchOut; currentHead: string | null }) {
  const headChanged = !!currentHead && currentHead !== patch.commit_sha && patch.apply_status !== "applied";
  const applyConflict = !!patch.apply_error;
  if (!headChanged && !applyConflict) return null;

  const patchFile = `probe-patch-${patch.id}.diff`;
  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200 space-y-2"
      data-testid="patch-recovery"
    >
      {headChanged && (
        <div>
          <div className="font-medium">Repository HEAD changed since this patch was generated.</div>
          <div>
            This patch was generated for <code>{patch.commit_sha?.slice(0, 8)}</code>; HEAD is now{" "}
            <code>{currentHead?.slice(0, 8)}</code>. It cannot be applied after HEAD changed. Create a
            new snapshot, rebuild the plan, and regenerate the patch from the{" "}
            <Link to="/repository" className="underline">Repository Refresh Hub</Link>.
          </div>
        </div>
      )}
      {applyConflict && (
        <div>
          <div className="font-medium">Apply the patch manually:</div>
          <ol className="list-decimal pl-4 space-y-0.5">
            <li>Download the patch, then check it before applying.</li>
            <li>If <code>git apply --check</code> fails on a dirty tree, commit / stash / revert first.</li>
            <li>If the conflict cannot be resolved, recreate the plan from the latest snapshot.</li>
          </ol>
        </div>
      )}
      <pre className="rounded bg-muted/70 p-2 overflow-x-auto text-[11px] leading-5">{[
        "git status --short",
        "git rev-parse HEAD",
        `git apply --check ${patchFile}`,
        `git apply ${patchFile}`,
      ].join("\n")}</pre>
    </div>
  );
}
