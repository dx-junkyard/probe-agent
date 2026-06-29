import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  useCapabilityHierarchy, useCapabilityHierarchyDrift,
  useGenerateCapabilityHierarchy, useRequestExplanationRefresh,
  useLatestSnapshot, useSymbols, useLatestDrafts,
  useSystemUnderstanding,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Map as MapIcon, Sparkles, Workflow, RefreshCw, Link2,
  Target, Boxes, Layers, Server, ChevronRight, AlertTriangle,
  CheckCircle2, Circle, MessageSquareText,
} from "lucide-react";
import type {
  CapabilityOut, CapabilityElementOut,
  SupportingElementOut, CapabilityPurposeOut, HierarchyProvenanceOut,
  AnchorDriftOut, CapabilityDriftOut, DriftStatus, HierarchyProvenanceKind,
  ExplanationRefreshOut,
} from "@/api/types";

// Keep source-authored, structural, and reasoning interpretations distinct (#56).
const PROVENANCE_LABEL: Record<string, string> = {
  source_authored: "source-authored",
  structural: "deterministic AST",
  reasoning_llm: "LLM interpretation",
  manual: "manual",
};
const PROVENANCE_STYLE: Record<string, string> = {
  source_authored: "border-emerald-300 text-emerald-700 dark:text-emerald-300",
  structural: "border-slate-300 text-slate-600 dark:text-slate-300",
  reasoning_llm: "border-violet-400 text-violet-700 dark:text-violet-300",
  manual: "border-sky-300 text-sky-700 dark:text-sky-300",
};

// Drift freshness (#57) — a review trigger, never a correctness verdict.
const DRIFT_STYLE: Record<string, string> = {
  fresh: "border-emerald-300 text-emerald-700 dark:text-emerald-300",
  partially_stale: "border-amber-300 text-amber-700 dark:text-amber-300",
  stale: "border-amber-400 text-amber-800 dark:text-amber-200",
  missing_source: "border-red-300 text-red-700 dark:text-red-300",
  unknown: "border-muted text-muted-foreground",
};
const DRIFT_LABEL: Record<string, string> = {
  fresh: "fresh", partially_stale: "partially stale", stale: "stale",
  missing_source: "missing source", unknown: "unknown",
};

const REVIEW_STATUSES: DriftStatus[] = ["partially_stale", "stale", "missing_source"];

type SelectedNode =
  | { kind: "purpose"; data: CapabilityPurposeOut }
  | { kind: "capability"; data: CapabilityOut }
  | { kind: "element"; data: CapabilityElementOut }
  | { kind: "supporting"; data: SupportingElementOut };

function ProvenanceBadges({ provenance }: { provenance: HierarchyProvenanceOut }) {
  const kind = provenance.provenance_kind as HierarchyProvenanceKind;
  return (
    <Badge variant="outline" className={`text-[10px] ${PROVENANCE_STYLE[kind] ?? ""}`}>
      {PROVENANCE_LABEL[kind] ?? kind}
    </Badge>
  );
}

function DriftBadge({ status }: { status: DriftStatus | undefined }) {
  if (!status) return null;
  return (
    <Badge variant="outline" className={`text-[10px] ${DRIFT_STYLE[status] ?? ""}`}>
      {DRIFT_LABEL[status] ?? status}
    </Badge>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground shrink-0 w-28">{label}</dt>
      <dd className="break-words min-w-0">{children}</dd>
    </div>
  );
}

// Suggestion-only explanation refresh (#59) reused for hierarchy nodes (#62).
function RefreshPanel({ data }: { data: ExplanationRefreshOut }) {
  const p = data.proposal;
  if (data.status === "failed" || !p || p.status === "failed") {
    return (
      <div className="rounded-md border border-red-300 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-2 py-1.5 text-[11px] text-red-800 dark:text-red-200">
        Refresh failed: {data.error ?? "the reasoning model did not return a valid proposal."}
      </div>
    );
  }
  return (
    <div className="rounded-md border border-sky-300 bg-sky-50 dark:bg-sky-950/20 dark:border-sky-800 px-2 py-2 text-[11px] space-y-1.5">
      <div className="rounded bg-amber-100 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200 px-2 py-1">
        {data.review_note}
      </div>
      <Field label="Drift">{p.drift_reason}</Field>
      <div>
        <div className="text-muted-foreground">Current explanation (source of truth):</div>
        <pre className="whitespace-pre-wrap break-words bg-muted/50 rounded p-1 mt-0.5">
          {p.old_explanation || "(none)"}
        </pre>
      </div>
      {p.proposed_explanation && (
        <div>
          <div className="text-muted-foreground">Proposed explanation (suggestion):</div>
          <pre className="whitespace-pre-wrap break-words bg-muted/50 rounded p-1 mt-0.5">
            {p.proposed_explanation}
          </pre>
        </div>
      )}
    </div>
  );
}

function DetailsPanel({
  selected, driftByNode,
}: {
  selected: SelectedNode;
  driftByNode: Map<number, AnchorDriftOut>;
}) {
  const refresh = useRequestExplanationRefresh();
  const [proposal, setProposal] = useState<ExplanationRefreshOut | null>(null);
  const { data: understanding } = useSystemUnderstanding();

  const data = selected.data;
  const provenance = data.provenance;

  const relatedGaps = useMemo(() => {
    if (!understanding?.gaps) return [];
    const capName = selected.kind === "capability" ? data.name : null;
    if (!capName) return [];
    return understanding.gaps.filter((g) => g.capability_key === capName);
  }, [understanding?.gaps, selected.kind, data.name]);
  const drift = driftByNode.get(data.id);
  const reviewRecommended = drift ? REVIEW_STATUSES.includes(drift.status) : false;
  const entrypointType = provenance.entrypoint_type;
  const entrypointRef = provenance.entrypoint_ref;

  const requestRefresh = async () => {
    setProposal(null);
    try {
      const result = await refresh.mutateAsync({ node_id: data.id });
      setProposal(result);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const flowLink = entrypointType && entrypointRef
    ? `/flow-explorer?entrypoint_type=${encodeURIComponent(entrypointType)}&entrypoint_id=${encodeURIComponent(entrypointRef)}`
    : null;

  return (
    <Card data-testid="capability-details">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
          <span className="break-words">{data.name}</span>
          <Badge variant="secondary" className="text-[10px]">{selected.kind}</Badge>
          {selected.kind === "element" && selected.data.classification && (
            <Badge variant="outline" className="text-[10px]">
              {selected.data.classification}
            </Badge>
          )}
        </CardTitle>
        {data.summary && (
          <CardDescription className="text-xs break-words">{data.summary}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-muted-foreground text-[11px]">Provenance:</span>
          <ProvenanceBadges provenance={provenance} />
          {drift && (
            <>
              <span className="text-muted-foreground text-[11px] ml-2">Freshness:</span>
              <DriftBadge status={drift.status} />
              {drift.changed_hashes.length > 0 && (
                <span className="text-[11px] text-muted-foreground">
                  changed: {drift.changed_hashes.join(", ")}
                </span>
              )}
            </>
          )}
        </div>

        <dl className="space-y-1.5">
          {selected.kind === "element" && selected.data.element_role && (
            <Field label="Role">{selected.data.element_role}</Field>
          )}
          {selected.kind === "element" && selected.data.operation_kind && (
            <Field label="Operation kind">{selected.data.operation_kind}</Field>
          )}
          {selected.kind === "element" && selected.data.probe_value && (
            <Field label="Probe value">{selected.data.probe_value}</Field>
          )}
          {selected.kind === "supporting" && selected.data.supporting_kind && (
            <Field label="Boundary">{selected.data.supporting_kind}</Field>
          )}
          {selected.kind === "capability" && selected.data.capability_key && (
            <Field label="Capability key">{selected.data.capability_key}</Field>
          )}
          {provenance.path && (
            <Field label="Source anchor">
              <span className="font-mono">
                {provenance.path}
                {provenance.start_line != null && provenance.end_line != null &&
                  `:${provenance.start_line}–${provenance.end_line}`}
              </span>
            </Field>
          )}
          {provenance.qualified_name && (
            <Field label="Symbol">
              <span className="font-mono">{provenance.qualified_name}</span>
            </Field>
          )}
          {provenance.feature_id && (
            <Field label="Feature Map">
              <span className="inline-flex items-center gap-1">
                <Link2 className="h-3 w-3" /> {provenance.feature_id}
              </span>
            </Field>
          )}
          {provenance.provider && provenance.provider !== "deterministic" && (
            <Field label="Reasoning model">
              {provenance.provider}/{provenance.model}
            </Field>
          )}
        </dl>

        {flowLink ? (
          <Link
            to={flowLink}
            data-testid="open-in-flow"
            className={cn(buttonVariants({ size: "sm", variant: "outline" }), "h-7 text-[11px] gap-1")}
          >
            <Workflow className="h-3 w-3" /> Open in Flow Explorer
          </Link>
        ) : (
          (selected.kind === "element" || selected.kind === "supporting") && (
            <p className="text-[11px] text-muted-foreground">
              No linked API/job/CLI entrypoint for this node, so it has no
              executable Flow Explorer graph. Source-authored elements without an
              entrypoint are still shown above for navigation.
            </p>
          )
        )}

        {reviewRecommended && (
          <div className="pt-1 space-y-2">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[11px] gap-1"
              onClick={requestRefresh}
              disabled={refresh.isPending}
              data-testid="request-refresh"
            >
              <RefreshCw className={`h-3 w-3 ${refresh.isPending ? "animate-spin" : ""}`} />
              {refresh.isPending ? "Proposing…" : "Propose explanation refresh"}
            </Button>
            <p className="text-[10px] text-muted-foreground">
              A refresh is a suggestion only. The target source repository stays
              the source of truth; nothing is written back.
            </p>
            {proposal && <RefreshPanel data={proposal} />}
          </div>
        )}

        {relatedGaps.length > 0 && (
          <div className="pt-2 space-y-1.5" data-testid="related-gaps">
            <p className="text-[11px] font-medium text-muted-foreground">Related gaps</p>
            {relatedGaps.map((g, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[11px]">
                <AlertTriangle className="h-3 w-3 text-yellow-600 shrink-0" />
                <span>{g.title ?? g.node_name ?? g.gap_type}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TreeRow({
  label, icon, active, depth, onClick, badge, drift,
}: {
  label: string;
  icon: ReactNode;
  active: boolean;
  depth: number;
  onClick: () => void;
  badge?: ReactNode;
  drift?: DriftStatus;
}) {
  return (
    <button
      onClick={onClick}
      style={{ paddingLeft: `${depth * 14 + 8}px` }}
      className={`w-full text-left rounded-md pr-2 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
        active ? "bg-secondary" : "hover:bg-secondary/50"
      }`}
    >
      <span className="shrink-0 text-muted-foreground">{icon}</span>
      <span className="truncate flex-1">{label}</span>
      {badge}
      {drift && drift !== "fresh" && drift !== "unknown" && (
        <span className={`h-1.5 w-1.5 rounded-full ${
          drift === "missing_source" ? "bg-red-500" : "bg-amber-500"
        }`} title={DRIFT_LABEL[drift]} />
      )}
    </button>
  );
}

// Required generation order (#62 follow-up): a snapshot must exist before
// symbols can be indexed, and a System Profile Draft (for the purpose node)
// is generated separately on the Repository page. Surfacing this as a
// checklist replaces a prose explanation that left the order implicit.
function PrerequisiteChecklist() {
  const { data: snapshot } = useLatestSnapshot();
  const { data: symbols } = useSymbols();
  const { data: drafts } = useLatestDrafts();

  const steps = [
    { label: "Snapshot created", done: !!snapshot },
    { label: "Symbols indexed", done: !!symbols && symbols.symbol_count > 0 },
    { label: "System Profile Draft generated", done: !!drafts?.system_profile_draft },
  ];

  return (
    <ul className="text-sm text-left max-w-xs mx-auto space-y-1.5">
      {steps.map((step) => (
        <li key={step.label} className="flex items-center gap-2">
          {step.done ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
          ) : (
            <Circle className="h-4 w-4 text-muted-foreground shrink-0" />
          )}
          <span className={cn(!step.done && "text-muted-foreground")}>{step.label}</span>
        </li>
      ))}
    </ul>
  );
}

export default function CapabilityMapPage() {
  const { data: hierarchy, isLoading } = useCapabilityHierarchy();
  const { data: driftData } = useCapabilityHierarchyDrift();
  const generate = useGenerateCapabilityHierarchy();
  const [selected, setSelected] = useState<SelectedNode | null>(null);

  // Flatten every drift anchor to its hierarchy node id for O(1) freshness lookup.
  const driftByNode = useMemo(() => {
    const map = new Map<number, AnchorDriftOut>();
    if (!driftData) return map;
    const add = (a: AnchorDriftOut | null) => { if (a) map.set(a.node_id, a); };
    add(driftData.purpose);
    for (const cap of driftData.capabilities) {
      cap.elements.forEach(add);
      cap.supporting_elements.forEach(add);
    }
    driftData.unclassified_elements.forEach(add);
    driftData.unattached_supporting.forEach(add);
    return map;
  }, [driftData]);

  const capDriftById = useMemo(() => {
    const map = new Map<number, CapabilityDriftOut>();
    driftData?.capabilities.forEach(c => map.set(c.capability_id, c));
    return map;
  }, [driftData]);

  const run = hierarchy?.intelligence_run;
  const hasHierarchy = !!run && (hierarchy?.capabilities.length || hierarchy?.purpose);

  const handleGenerate = (useReasoning: boolean) => {
    generate.mutateAsync(useReasoning)
      .then((result) => {
        if (result.intelligence_run?.status === "failed") {
          toast.error(result.intelligence_run.error_details || "Hierarchy generation failed");
          return;
        }
        toast.success(`Capability hierarchy generated: ${result.capabilities.length} capability(ies)`);
        setSelected(null);
      })
      .catch(e => toast.error(String(e)));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <MapIcon className="h-6 w-6" /> Capability Map
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Navigate from system purpose down to core capabilities, the APIs and
            functions that implement them, their boundaries, and probe flows.
            Source-authored facts, deterministic structure, and reasoning-model
            interpretations stay visibly distinct.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => handleGenerate(true)}
            disabled={generate.isPending}
          >
            <Sparkles className="h-4 w-4 mr-1" />
            {generate.isPending ? "Generating…" : "Generate with reasoning"}
          </Button>
          <Button
            size="sm"
            onClick={() => handleGenerate(false)}
            disabled={generate.isPending}
            data-testid="generate-hierarchy"
          >
            <Sparkles className="h-4 w-4 mr-1" />
            {generate.isPending ? "Generating…" : "Generate capability hierarchy"}
          </Button>
        </div>
      </div>

      {hierarchy?.is_mock && (
        <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          This hierarchy used a mock LLM provider and is for development purposes only.
        </div>
      )}
      {driftData?.is_review_recommended && driftData.review_note && (
        <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-4 py-3 text-sm text-amber-900 dark:text-amber-100 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{driftData.review_note}</span>
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : !hasHierarchy ? (
        <Card>
          <CardContent className="py-10 text-center space-y-2">
            <p className="text-sm font-medium">No capability hierarchy yet.</p>
            <p className="text-sm text-muted-foreground max-w-xl mx-auto">
              Complete these steps on the{" "}
              <Link to="/repository" className="underline">Repository</Link> page,
              in order, then generate the capability hierarchy. The hierarchy is
              built from source-authored <code>probe-agent</code> metadata; APIs
              without a capability stay unclassified rather than being guessed at.
            </p>
            <PrerequisiteChecklist />
            <div className="flex justify-center gap-2 flex-wrap mt-2">
              <Link
                to="/interview"
                className={buttonVariants({ size: "sm", variant: "outline" })}
                data-testid="start-interview-empty"
              >
                <MessageSquareText className="h-4 w-4 mr-1" />
                Start interview
              </Link>
              <Button
                size="sm"
                onClick={() => handleGenerate(false)}
                disabled={generate.isPending}
                data-testid="generate-hierarchy-empty"
              >
                <Sparkles className="h-4 w-4 mr-1" />
                {generate.isPending ? "Generating…" : "Generate capability hierarchy"}
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
          {/* Left: hierarchy tree */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Hierarchy</CardTitle>
              <CardDescription className="text-xs">
                {hierarchy!.capabilities.length} capability(ies)
                {run && <span> · {run.decision_method}</span>}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-0.5 max-h-[36rem] overflow-y-auto">
              {hierarchy!.purpose && (
                <TreeRow
                  label={hierarchy!.purpose.name}
                  icon={<Target className="h-3.5 w-3.5" />}
                  depth={0}
                  active={selected?.kind === "purpose"}
                  onClick={() => setSelected({ kind: "purpose", data: hierarchy!.purpose! })}
                  drift={driftByNode.get(hierarchy!.purpose.id)?.status}
                />
              )}
              {hierarchy!.capabilities.map(cap => (
                <div key={cap.id}>
                  <TreeRow
                    label={cap.name}
                    icon={<Boxes className="h-3.5 w-3.5" />}
                    depth={hierarchy!.purpose ? 1 : 0}
                    active={selected?.kind === "capability" && selected.data.id === cap.id}
                    onClick={() => setSelected({ kind: "capability", data: cap })}
                    drift={capDriftById.get(cap.id)?.status}
                  />
                  {cap.elements.map(el => (
                    <TreeRow
                      key={el.id}
                      label={el.name}
                      icon={<Layers className="h-3.5 w-3.5" />}
                      depth={hierarchy!.purpose ? 2 : 1}
                      active={selected?.kind === "element" && selected.data.id === el.id}
                      onClick={() => setSelected({ kind: "element", data: el })}
                      drift={driftByNode.get(el.id)?.status}
                      badge={el.provenance.entrypoint_ref ? (
                        <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
                      ) : undefined}
                    />
                  ))}
                  {cap.supporting_elements.map(s => (
                    <TreeRow
                      key={s.id}
                      label={s.name}
                      icon={<Server className="h-3.5 w-3.5" />}
                      depth={hierarchy!.purpose ? 2 : 1}
                      active={selected?.kind === "supporting" && selected.data.id === s.id}
                      onClick={() => setSelected({ kind: "supporting", data: s })}
                      drift={driftByNode.get(s.id)?.status}
                    />
                  ))}
                </div>
              ))}

              {hierarchy!.unclassified_elements.length > 0 && (
                <div className="pt-2">
                  <div className="px-2 py-1 flex items-center justify-between gap-2">
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      Unclassified
                    </span>
                    <Link
                      to="/interview"
                      className="inline-flex items-center text-[10px] text-primary hover:underline"
                    >
                      <MessageSquareText className="h-3 w-3 mr-1" />
                      Interview
                    </Link>
                  </div>
                  {hierarchy!.unclassified_elements.map(el => (
                    <TreeRow
                      key={el.id}
                      label={el.name}
                      icon={<Layers className="h-3.5 w-3.5" />}
                      depth={1}
                      active={selected?.kind === "element" && selected.data.id === el.id}
                      onClick={() => setSelected({ kind: "element", data: el })}
                      drift={driftByNode.get(el.id)?.status}
                      badge={el.provenance.entrypoint_ref ? (
                        <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
                      ) : undefined}
                    />
                  ))}
                </div>
              )}
              {hierarchy!.unattached_supporting.length > 0 && (
                <div className="pt-2">
                  <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                    Unattached boundaries
                  </div>
                  {hierarchy!.unattached_supporting.map(s => (
                    <TreeRow
                      key={s.id}
                      label={s.name}
                      icon={<Server className="h-3.5 w-3.5" />}
                      depth={1}
                      active={selected?.kind === "supporting" && selected.data.id === s.id}
                      onClick={() => setSelected({ kind: "supporting", data: s })}
                      drift={driftByNode.get(s.id)?.status}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Right: details */}
          <div className="space-y-4">
            {selected ? (
              <DetailsPanel selected={selected} driftByNode={driftByNode} />
            ) : (
              <Card>
                <CardContent className="py-12 text-center text-sm text-muted-foreground">
                  Select a purpose, capability, element, or boundary to see its
                  source anchor, provenance, freshness, and navigation actions.
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
