import {
  useApplyRemovalPatch, useCreatePlanFromReconciliation, useCreateProbePattern,
  useGenerateRemovalPatch, useInstrumentationScan, useInvestigateReconcilePoint,
  usePatternRemovalPatches, useProbePattern, useProbePatterns,
  useReconcilePointDecision, useReconcileProbePattern, useUpdateProbePattern,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import {
  Archive, ArchiveRestore, BookMarked, CheckCircle, Download, Eraser,
  FileCode, HelpCircle, Pencil, RefreshCw, ScanSearch, XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type {
  InstrumentedProbeOut, ProbePatternOut, ProbeRemovalPatchOut,
  ReconcileClassification, ReconcilePointOut,
} from "@/api/types";

const CLASSIFICATION_META: Record<ReconcileClassification, {
  label: string;
  variant: "success" | "secondary" | "warning" | "outline" | "destructive";
  hint: string;
}> = {
  exact_match: {
    label: "exact match", variant: "success",
    hint: "pathとsignatureが一致 — そのまま再接続できます。",
  },
  moved_match: {
    label: "moved", variant: "secondary",
    hint: "実装が移動またはrenameされた可能性があります。",
  },
  changed_signature: {
    label: "signature changed", variant: "warning",
    hint: "役割は同じですが、pattern保存後にinterfaceが変わっています。",
  },
  split_or_merged: {
    label: "split / merged", variant: "warning",
    hint: "責務が他のsymbolに分割または統合されたようです。",
  },
  missing: {
    label: "missing", variant: "destructive",
    hint: "現在対応するものが見つかりませんでした。",
  },
  unsafe: {
    label: "unsafe", variant: "destructive",
    hint: "現在のtargetがside-effect safety denylistに該当します。",
  },
};

export default function ProbePatternsPage() {
  const { data: patternsData, isLoading } = useProbePatterns();
  const [scanOpen, setScanOpen] = useState(false);
  const [expandedPattern, setExpandedPattern] = useState<number | null>(null);

  const patterns = patternsData?.patterns ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Probe Patterns</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Reusable observation units: save probes before a production
            release, then reconcile them against the changed implementation
            when development resumes.
          </p>
        </div>
        <Button size="sm" onClick={() => setScanOpen(v => !v)}>
          <ScanSearch className="h-4 w-4 mr-1" />
          {scanOpen ? "Hide Scan" : "Scan Instrumentation"}
        </Button>
      </div>

      {scanOpen && <InstrumentationScanPanel patterns={patterns} />}

      {isLoading ? (
        <div className="space-y-4">{[1, 2].map(i => <Skeleton key={i} className="h-32 w-full" />)}</div>
      ) : !patterns.length ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No probe patterns yet. Scan the current instrumentation and save
            probes as a pattern before removing them for release.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {patterns.map(p => (
            <PatternCard
              key={p.id}
              pattern={p}
              expanded={expandedPattern === p.id}
              onToggle={() => setExpandedPattern(expandedPattern === p.id ? null : p.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Pre-release scan & save flow ────────────────────────────────────

function InstrumentationScanPanel({ patterns }: { patterns: ProbePatternOut[] }) {
  const { data, isLoading, error } = useInstrumentationScan(true);
  const createPattern = useCreateProbePattern();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saveOpen, setSaveOpen] = useState(false);
  const [name, setName] = useState("");
  const [featureId, setFeatureId] = useState("");
  const [objective, setObjective] = useState("");
  const [description, setDescription] = useState("");

  const probes = data?.probes ?? [];
  const key = (p: InstrumentedProbeOut) => `${p.path}:${p.symbol}`;
  const toggle = (p: InstrumentedProbeOut) => {
    const next = new Set(selected);
    const k = key(p);
    if (next.has(k)) next.delete(k); else next.add(k);
    setSelected(next);
  };

  const openSave = () => {
    const chosen = probes.filter(p => selected.has(key(p)));
    const linked = chosen.find(p => p.linked_objective);
    setObjective(linked?.linked_objective ?? "");
    setFeatureId(chosen.find(p => p.linked_feature_id)?.linked_feature_id ?? "");
    setSaveOpen(true);
  };

  const save = async () => {
    const chosen = probes.filter(p => selected.has(key(p)));
    try {
      await createPattern.mutateAsync({
        name: name.trim(),
        feature_id: featureId.trim(),
        objective: objective.trim(),
        description: description.trim(),
        origin: "scan",
        source_plan_id: chosen.find(p => p.linked_plan_id)?.linked_plan_id ?? null,
        points: chosen.map(p => ({
          path: p.path,
          symbol: p.symbol,
          component_id: p.component_id ?? "",
          reason: p.linked_reason ?? "",
          recommended_mode: p.linked_recommended_mode ?? "trace",
        })),
      });
      toast.success("Pattern saved");
      setSaveOpen(false);
      setSelected(new Set());
      setName("");
      setDescription("");
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Eraser className="h-4 w-4" />
          Pre-release probe removal
        </CardTitle>
        <CardDescription>
          Probes found in the latest committed snapshot
          {data && <> (<code className="font-mono text-xs">{data.commit_sha.slice(0, 8)}</code>)</>}.
          Save them as a pattern first so their purpose survives the removal,
          then generate the removal patch from the pattern below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : error ? (
          <div className="text-sm text-muted-foreground">
            {String((error as Error).message ?? error)}
          </div>
        ) : !probes.length ? (
          <div className="text-sm text-muted-foreground">
            No @probe instrumentation found in the latest snapshot.
          </div>
        ) : (
          <>
            <div className="space-y-2">
              {probes.map(p => {
                const inPatterns = p.pattern_ids
                  .map(id => patterns.find(x => x.id === id)?.name)
                  .filter(Boolean);
                return (
                  <label
                    key={key(p)}
                    className="flex items-start gap-3 rounded-lg border p-3 text-sm cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selected.has(key(p))}
                      onChange={() => toggle(p)}
                    />
                    <div className="space-y-1 min-w-0">
                      <div className="font-mono text-xs">{p.path}:{p.symbol}</div>
                      <div className="text-xs text-muted-foreground">
                        Lines {p.line_start}–{p.line_end}
                        {p.component_id && <> · component: {p.component_id}</>}
                      </div>
                      {p.linked_reason && (
                        <div className="text-xs">
                          <span className="text-muted-foreground">Plan #{p.linked_plan_id} ({p.linked_feature_id}): </span>
                          {p.linked_reason}
                        </div>
                      )}
                      {inPatterns.length > 0 && (
                        <div className="text-xs text-muted-foreground">
                          Already in pattern: {inPatterns.join(", ")}
                        </div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
            <Button size="sm" disabled={selected.size === 0} onClick={openSave}>
              <BookMarked className="h-4 w-4 mr-1" />
              Save {selected.size || ""} selected as pattern
            </Button>
          </>
        )}
      </CardContent>

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogHeader>
          <DialogTitle>Save Probe Pattern</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Name</Label>
            <Input value={name} onChange={e => setName(e.target.value)} placeholder="invoice-total-observation" />
          </div>
          <div className="space-y-2">
            <Label>Feature</Label>
            <Input value={featureId} onChange={e => setFeatureId(e.target.value)} placeholder="feature-id (optional)" />
          </div>
          <div className="space-y-2">
            <Label>What does this pattern observe, and why?</Label>
            <Textarea
              value={objective}
              onChange={e => setObjective(e.target.value)}
              placeholder="e.g. Verify invoice totals stay correct across tax/discount changes"
              rows={3}
            />
          </div>
          <div className="space-y-2">
            <Label>Notes</Label>
            <Textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} />
          </div>
          <Button
            className="w-full"
            disabled={!name.trim() || createPattern.isPending}
            onClick={save}
          >
            {createPattern.isPending ? "Saving..." : "Save Pattern"}
          </Button>
        </div>
      </Dialog>
    </Card>
  );
}

// ── Pattern card ────────────────────────────────────────────────────

function PatternCard({ pattern, expanded, onToggle }: {
  pattern: ProbePatternOut;
  expanded: boolean;
  onToggle: () => void;
}) {
  const statusVariant = pattern.status === "active" ? "success"
    : pattern.status === "stale" ? "warning"
    : "secondary";
  const rec = pattern.latest_reconciliation;

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={onToggle}>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
              {pattern.name}
              <Badge variant={statusVariant}>{pattern.status}</Badge>
              <Badge variant="outline" className="text-[10px]">{pattern.origin}</Badge>
              {pattern.pending_decision_count > 0 && (
                <Badge variant="warning">{pattern.pending_decision_count} to decide</Badge>
              )}
            </CardTitle>
            <CardDescription className="mt-1">
              {pattern.feature_id && <span className="mr-2">Feature: {pattern.feature_id}</span>}
              {pattern.objective || "No objective recorded"}
            </CardDescription>
            <div className="mt-2 flex items-center gap-2 flex-wrap text-xs text-muted-foreground">
              <span>{pattern.point_count} probe point{pattern.point_count === 1 ? "" : "s"}</span>
              {pattern.removed_point_count > 0 && (
                <span>· {pattern.removed_point_count} removed from production</span>
              )}
              {pattern.last_reconciled_at && (
                <span>· reconciled {formatTimestamp(pattern.last_reconciled_at)}</span>
              )}
              {pattern.last_used_at && (
                <span>· used {formatTimestamp(pattern.last_used_at)}</span>
              )}
            </div>
            {rec && rec.status === "completed" && (
              <div className="mt-2 flex items-center gap-1 flex-wrap">
                {Object.entries(rec.summary).map(([cls, n]) => {
                  const meta = CLASSIFICATION_META[cls as ReconcileClassification];
                  return meta ? (
                    <Badge key={cls} variant={meta.variant} className="text-[10px]">
                      {n} {meta.label}
                    </Badge>
                  ) : null;
                })}
              </div>
            )}
          </div>
          <span className="text-xs text-muted-foreground shrink-0">
            saved from <code className="font-mono">{pattern.source_commit_sha.slice(0, 8)}</code>
          </span>
        </div>
      </CardHeader>
      {expanded && <PatternDetail patternId={pattern.id} />}
    </Card>
  );
}

function PatternDetail({ patternId }: { patternId: number }) {
  const { data: pattern } = useProbePattern(patternId);
  const { data: removalPatches } = usePatternRemovalPatches(patternId);
  const reconcile = useReconcileProbePattern();
  const update = useUpdateProbePattern();
  const generateRemoval = useGenerateRemovalPatch();
  const createPlan = useCreatePlanFromReconciliation();
  const [editOpen, setEditOpen] = useState(false);

  if (!pattern) return <CardContent><Skeleton className="h-24 w-full" /></CardContent>;
  const rec = pattern.latest_reconciliation;
  const archived = pattern.status === "archived";

  const runReconcile = () =>
    reconcile.mutateAsync(pattern.id)
      .then(r => {
        if (r.status === "failed") toast.error(`Reconcileに失敗しました: ${r.error}`);
        else toast.success("最新snapshotとreconcileしました");
      })
      .catch(e => toast.error(String(e)));

  return (
    <CardContent className="space-y-5">
      <div className="flex gap-2 flex-wrap">
        <Button size="sm" onClick={runReconcile} disabled={reconcile.isPending || archived}>
          <RefreshCw className="h-4 w-4 mr-1" />
          {reconcile.isPending ? "Reconcile中..." : "最新snapshotとReconcile"}
        </Button>
        <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
          <Pencil className="h-4 w-4 mr-1" /> 編集
        </Button>
        <Button
          size="sm" variant="outline"
          onClick={() =>
            update.mutateAsync({ patternId: pattern.id, status: archived ? "active" : "archived" })
              .then(() => toast.success(archived ? "Patternを復元しました" : "Patternをarchiveしました"))
              .catch(e => toast.error(String(e)))
          }
        >
          {archived
            ? <><ArchiveRestore className="h-4 w-4 mr-1" /> 復元</>
            : <><Archive className="h-4 w-4 mr-1" /> Archive</>}
        </Button>
        <Button
          size="sm" variant="outline"
          disabled={generateRemoval.isPending || pattern.points.every(p => p.status !== "saved")}
          onClick={() =>
            generateRemoval.mutateAsync({ patternId: pattern.id })
              .then(p => {
                if (p.status === "failed") toast.error(`パッチ生成に失敗しました: ${p.error}`);
                else toast.success("除去パッチを生成しました — 下のdiffを確認してください");
              })
              .catch(e => toast.error(String(e)))
          }
        >
          <Eraser className="h-4 w-4 mr-1" /> 除去パッチを生成
        </Button>
      </div>

      <div>
        <h4 className="text-sm font-medium mb-2">Probe Points</h4>
        <div className="space-y-2">
          {pattern.points.map(pt => (
            <div key={pt.id} className="rounded-lg border p-3 text-sm space-y-1">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs">{pt.path}:{pt.symbol}</span>
                <Badge variant={pt.status === "saved" ? "secondary" : "outline"}>
                  {pt.status === "saved" ? "saved" : "removed from production"}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground">
                Mode: {pt.recommended_mode} · Risk: {pt.side_effect_risk}
                {pt.signature && <> · <code className="font-mono">{pt.signature}</code></>}
                {pt.removed_at && <> · removed {formatTimestamp(pt.removed_at)}</>}
              </div>
              {pt.reason && <div className="text-xs">{pt.reason}</div>}
            </div>
          ))}
        </div>
      </div>

      {rec && (
        <ReconciliationSection
          pattern={pattern}
          onCreatePlan={(objective?: string) =>
            createPlan.mutateAsync({
              patternId: pattern.id,
              reconciliationId: rec.id,
              objective,
            })
              .then(plan => toast.success(`Probe plan #${plan.id} created — approve its points in the Probe Planner`))
              .catch(e => toast.error(String(e)))
          }
          createPending={createPlan.isPending}
        />
      )}

      {(removalPatches?.length ?? 0) > 0 && (
        <RemovalPatchesSection patches={removalPatches!} />
      )}

      {pattern.events.length > 0 && (
        <div>
          <h4 className="text-sm font-medium mb-2">History</h4>
          <div className="space-y-1">
            {pattern.events.map(e => (
              <div key={e.id} className="text-xs text-muted-foreground flex gap-2">
                <span className="shrink-0">{formatTimestamp(e.created_at)}</span>
                <span className="font-medium text-foreground">{e.event_type}</span>
                {"plan_id" in e.detail && <span>plan #{String(e.detail.plan_id)}</span>}
                {"patch_id" in e.detail && <span>patch #{String(e.detail.patch_id)}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      <EditPatternDialog
        pattern={pattern}
        open={editOpen}
        onOpenChange={setEditOpen}
      />
    </CardContent>
  );
}

function EditPatternDialog({ pattern, open, onOpenChange }: {
  pattern: ProbePatternOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateProbePattern();
  const [name, setName] = useState(pattern.name);
  const [featureId, setFeatureId] = useState(pattern.feature_id);
  const [objective, setObjective] = useState(pattern.objective);
  const [description, setDescription] = useState(pattern.description);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Edit Pattern</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Name</Label>
          <Input value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label>Feature</Label>
          <Input value={featureId} onChange={e => setFeatureId(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label>Objective</Label>
          <Textarea value={objective} onChange={e => setObjective(e.target.value)} rows={3} />
        </div>
        <div className="space-y-2">
          <Label>Notes</Label>
          <Textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} />
        </div>
        <Button
          className="w-full"
          disabled={!name.trim() || update.isPending}
          onClick={() =>
            update.mutateAsync({
              patternId: pattern.id,
              name: name.trim(),
              feature_id: featureId,
              objective,
              description,
            })
              .then(() => { toast.success("Pattern updated"); onOpenChange(false); })
              .catch(e => toast.error(String(e)))
          }
        >
          {update.isPending ? "Saving..." : "Save"}
        </Button>
      </div>
    </Dialog>
  );
}

// ── Reconciliation results ──────────────────────────────────────────

function ReconciliationSection({ pattern, onCreatePlan, createPending }: {
  pattern: ProbePatternOut;
  onCreatePlan: (objective?: string) => void;
  createPending: boolean;
}) {
  const rec = pattern.latest_reconciliation!;
  const pointsById = Object.fromEntries(pattern.points.map(p => [p.id, p]));
  const eligible = rec.status === "completed" && rec.points.some(p =>
    (p.classification === "exact_match" && p.user_decision !== "rejected")
    || (p.user_decision === "accepted" && p.target_symbol
        && p.classification !== "missing" && p.classification !== "unsafe"),
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">
          Latest reconciliation
          <span className="ml-2 text-xs text-muted-foreground font-normal">
            against <code className="font-mono">{rec.commit_sha.slice(0, 8)}</code> · {formatTimestamp(rec.created_at)}
          </span>
        </h4>
        {rec.status === "completed" && (
          <Button size="sm" disabled={!eligible || createPending} onClick={() => onCreatePlan()}>
            <FileCode className="h-4 w-4 mr-1" />
            {createPending ? "Creating..." : "Create Probe Plan"}
          </Button>
        )}
      </div>

      {rec.status === "failed" && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-200">
          Reconciliation failed: {rec.error}. Deterministic results below remain
          valid; configure a reasoning model and reconcile again for the rest.
        </div>
      )}
      {rec.is_mock && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200">
          Mock provider — reasoning classifications are unavailable.
        </div>
      )}

      <div className="space-y-2">
        {rec.points.map(rp => (
          <ReconcilePointCard
            key={rp.id}
            point={rp}
            patternPoint={pointsById[rp.pattern_point_id]}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Exact matches are included in a new plan automatically; moved / changed
        points need your confirmation first. The plan still goes through the
        normal approval, validation, and apply gates in the{" "}
        <Link to="/probe-planner" className="underline">Probe Planner</Link>.
      </p>
    </div>
  );
}

function ReconcilePointCard({ point, patternPoint }: {
  point: ReconcilePointOut;
  patternPoint?: { path: string; symbol: string };
}) {
  const decide = useReconcilePointDecision();
  const investigate = useInvestigateReconcilePoint();
  const meta = CLASSIFICATION_META[point.classification];
  const needsDecision = point.classification !== "exact_match"
    && point.classification !== "unsafe";
  const moved = point.target_symbol
    && patternPoint
    && (point.target_path !== patternPoint.path || point.target_symbol !== patternPoint.symbol);

  return (
    <div className="rounded-lg border p-3 text-sm space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant={meta.variant}>{meta.label}</Badge>
        <Badge variant="outline" className="text-[10px]">{point.decision_method}</Badge>
        {point.body_changed && (
          <Badge variant="outline" className="text-[10px]">body changed</Badge>
        )}
        {point.user_decision !== "pending" && (
          <Badge variant={point.user_decision === "accepted" ? "success" : "destructive"}>
            {point.user_decision}
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">{meta.hint}</span>
      </div>

      <div className="font-mono text-xs">
        {patternPoint && <span>{patternPoint.path}:{patternPoint.symbol}</span>}
        {moved && <span> → {point.target_path}:{point.target_symbol}</span>}
      </div>

      {point.hypothesis && <div className="text-xs"><span className="font-medium">仮説:</span> {point.hypothesis}</div>}
      {point.explanation && <div className="text-xs text-muted-foreground">{point.explanation}</div>}
      {point.denylist_hit && (
        <div className="text-xs text-red-600 dark:text-red-400">Denylist: {point.denylist_hit}</div>
      )}

      {point.evidence.length > 0 && (
        <div className="space-y-0.5">
          {point.evidence.map((ev, i) => (
            <div key={i} className="text-xs text-muted-foreground">
              <code className="font-mono">{ev.path}:{ev.start_line}-{ev.end_line}</code>
              {ev.summary && <> — {ev.summary}</>}
            </div>
          ))}
        </div>
      )}

      {needsDecision && point.question && (
        <div className="rounded-md border bg-secondary/30 p-3 space-y-2">
          <div className="text-xs font-medium">{point.question}</div>
          <div className="flex gap-2 flex-wrap">
            <Button
              size="sm" variant="outline"
              disabled={decide.isPending || point.user_decision === "accepted"}
              onClick={() =>
                decide.mutateAsync({ pointId: point.id, decision: "accepted" })
                  .catch(e => toast.error(String(e)))
              }
            >
              <CheckCircle className="h-3 w-3 mr-1 text-emerald-600" /> はい
            </Button>
            <Button
              size="sm" variant="outline"
              disabled={decide.isPending || point.user_decision === "rejected"}
              onClick={() =>
                decide.mutateAsync({ pointId: point.id, decision: "rejected" })
                  .catch(e => toast.error(String(e)))
              }
            >
              <XCircle className="h-3 w-3 mr-1 text-red-500" /> いいえ
            </Button>
            <Button
              size="sm" variant="outline"
              disabled={investigate.isPending}
              onClick={() =>
                investigate.mutateAsync(point.id)
                  .then(() => toast.success("調査が完了しました"))
                  .catch(e => toast.error(String(e)))
              }
            >
              <HelpCircle className="h-3 w-3 mr-1" />
              {investigate.isPending ? "調査中..." : "わからないので調べる"}
            </Button>
          </div>
        </div>
      )}

      {point.investigation && (
        <div className="rounded-md border border-sky-200 bg-sky-50 dark:border-sky-800 dark:bg-sky-950/20 p-3 space-y-1 text-xs">
          <div className="font-medium">調査結果</div>
          <div>{point.investigation.summary}</div>
          <div><span className="font-medium">推奨:</span> {point.investigation.recommendation}</div>
          {point.investigation.proposed_target_symbol && (
            <div className="font-mono">
              提案先: {point.investigation.proposed_target_path}:{point.investigation.proposed_target_symbol}
            </div>
          )}
          {point.investigation.evidence.map((ev, i) => (
            <div key={i} className="text-muted-foreground">
              <code className="font-mono">{ev.path}:{ev.start_line}-{ev.end_line}</code>
              {ev.summary && <> — {ev.summary}</>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Removal patches ─────────────────────────────────────────────────

function RemovalPatchesSection({ patches }: { patches: ProbeRemovalPatchOut[] }) {
  const applyPatch = useApplyRemovalPatch();
  const [applyTarget, setApplyTarget] = useState<ProbeRemovalPatchOut | null>(null);
  const [confirmation, setConfirmation] = useState("");

  return (
    <div>
      <h4 className="text-sm font-medium mb-2">除去パッチ</h4>
      <div className="space-y-2">
        {patches.map(patch => (
          <div key={patch.id} className="rounded-lg border p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Badge variant={patch.apply_status === "applied" ? "success" : patch.status === "failed" ? "destructive" : "secondary"}>
                  {patch.apply_status === "applied" ? "applied" : patch.status}
                </Badge>
                <span className="font-mono text-xs">{patch.commit_sha.slice(0, 8)}</span>
                <span className="text-xs text-muted-foreground">{formatTimestamp(patch.created_at)}</span>
              </div>
              <div className="flex gap-1">
                {patch.apply_status !== "applied" && patch.status !== "failed" && (
                  <Button
                    size="sm" variant="destructive"
                    onClick={() => { setApplyTarget(patch); setConfirmation(""); }}
                    disabled={applyPatch.isPending}
                  >
                    適用
                  </Button>
                )}
                {patch.diff && (
                  <Button
                    size="sm" variant="ghost"
                    onClick={() => {
                      const blob = new Blob([patch.diff], { type: "text/plain" });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url; a.download = `probe-removal-${patch.id}.diff`; a.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    <Download className="h-3 w-3 mr-1" /> ダウンロード
                  </Button>
                )}
              </div>
            </div>
            {patch.skipped.length > 0 && (
              <div className="text-xs text-muted-foreground">Skipped: {patch.skipped.join("; ")}</div>
            )}
            {patch.apply_error && (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-200">
                適用に失敗しました: {patch.apply_error}
              </div>
            )}
            {patch.diff && (
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs font-mono max-h-64 overflow-y-auto">
                {patch.diff}
              </pre>
            )}
            {patch.apply_status === "applied" && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-200">
                working tree からprobeを除去しました（commitは作成していません）。レビュー・
                テスト実行・commit・新しいsnapshotの作成をリリース前に行ってください。
              </div>
            )}
          </div>
        ))}
      </div>

      <Dialog
        open={applyTarget !== null}
        onOpenChange={(open) => { if (!open) { setApplyTarget(null); setConfirmation(""); } }}
      >
        <DialogHeader>
          <DialogTitle>除去パッチをRepositoryに適用</DialogTitle>
        </DialogHeader>
        {applyTarget && (
          <div className="space-y-4">
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-100">
              対象repositoryのworking treeから@probe計装を除去します。patternは観測
              文脈を保持するため、probeは後で再接続できます。
            </div>
            <div className="space-y-1 text-sm">
              <div>Snapshot commit: <code className="font-mono text-xs">{applyTarget.commit_sha}</code></div>
              <div>repositoryのHEADがこのcommitと一致し、working treeがcleanである必要があります。</div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">確認のため REMOVE と入力してください</label>
              <Input value={confirmation} onChange={e => setConfirmation(e.target.value)} placeholder="REMOVE" />
            </div>
            <Button
              variant="destructive"
              className="w-full"
              disabled={confirmation !== "REMOVE" || applyPatch.isPending}
              onClick={() =>
                applyPatch.mutateAsync({
                  patchId: applyTarget.id,
                  expectedCommitSha: applyTarget.commit_sha,
                }).then(() => {
                  toast.success("除去パッチを適用しました");
                  setApplyTarget(null);
                  setConfirmation("");
                }).catch(e => toast.error(String(e)))
              }
            >
              {applyPatch.isPending ? "適用中..." : "repositoryからprobeを除去"}
            </Button>
          </div>
        )}
      </Dialog>
    </div>
  );
}
