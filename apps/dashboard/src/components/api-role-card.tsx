import { useState } from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { useRequestExplanationRefresh } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AlertTriangle, Info, RefreshCw } from "lucide-react";
import type { ApiRoleCardOut, ExplanationRefreshOut } from "@/api/types";

export const PROVENANCE_LABEL: Record<string, string> = {
  source_authored: "source-authored",
  structural: "deterministic AST",
  reasoning_llm: "LLM interpretation",
  manual: "manual",
};
export const PROVENANCE_STYLE: Record<string, string> = {
  source_authored: "border-emerald-300 text-emerald-700 dark:text-emerald-300",
  structural: "border-slate-300 text-slate-600 dark:text-slate-300",
  reasoning_llm: "border-violet-400 text-violet-700 dark:text-violet-300",
  manual: "border-sky-300 text-sky-700 dark:text-sky-300",
};

export const DRIFT_STYLE: Record<string, string> = {
  fresh: "border-emerald-300 text-emerald-700 dark:text-emerald-300",
  partially_stale: "border-amber-300 text-amber-700 dark:text-amber-300",
  stale: "border-amber-400 text-amber-800 dark:text-amber-200",
  missing_source: "border-red-300 text-red-700 dark:text-red-300",
  unknown: "border-muted text-muted-foreground",
};
export const DRIFT_LABEL: Record<string, string> = {
  fresh: "fresh", partially_stale: "partially stale", stale: "stale",
  missing_source: "missing source", unknown: "unknown",
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground shrink-0 w-24">{label}</dt>
      <dd className="break-words min-w-0">{children}</dd>
    </div>
  );
}

function RefreshProposalPanel({ data }: { data: ExplanationRefreshOut }) {
  const p = data.proposal;
  if (data.status === "failed" || !p || p.status === "failed") {
    return (
      <div
        data-testid="refresh-proposal"
        className="rounded-md border border-red-300 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-2 py-1.5 text-[11px] text-red-800 dark:text-red-200"
      >
        Refresh failed: {data.error ?? "the reasoning model did not return a valid proposal."}
      </div>
    );
  }
  return (
    <div
      data-testid="refresh-proposal"
      className="rounded-md border border-sky-300 bg-sky-50 dark:bg-sky-950/20 dark:border-sky-800 px-2 py-2 text-[11px] space-y-1.5"
    >
      <div className="rounded bg-amber-100 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200 px-2 py-1">
        {data.review_note}
      </div>
      {p.summary_of_changes && (
        <Field label="What changed">{p.summary_of_changes}</Field>
      )}
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
      {p.proposed_metadata && (
        <div>
          <div className="text-muted-foreground">Proposed metadata (suggestion):</div>
          <pre className="whitespace-pre-wrap break-words bg-muted/50 rounded p-1 mt-0.5">
            {JSON.stringify(p.proposed_metadata, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export function ApiRoleCard({ card }: { card: ApiRoleCardOut }) {
  const classified = card.classification === "classified";
  const refresh = useRequestExplanationRefresh();
  const [proposal, setProposal] = useState<ExplanationRefreshOut | null>(null);

  const requestRefresh = async () => {
    setProposal(null);
    try {
      const result = await refresh.mutateAsync({
        entrypoint_type: card.entrypoint_type,
        entrypoint_id: card.entrypoint_id,
      });
      setProposal(result);
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <Card data-testid="api-role-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
          <Info className="h-4 w-4 shrink-0" />
          <span className="break-words">API Role</span>
          {card.source === "reasoning_llm" && (
            <Badge variant="outline" className="text-[10px] border-violet-400 text-violet-700 dark:text-violet-300">
              LLM scan
            </Badge>
          )}
          {classified ? (
            <Badge variant="secondary" className="text-[10px]">classified</Badge>
          ) : (
            <Badge variant="outline" className="text-[10px]">unclassified</Badge>
          )}
        </CardTitle>
        <CardDescription className="text-xs break-words">
          {card.label}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        {card.review_needed && (
          <div className="rounded-md border border-red-300 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-2 py-1.5 text-[11px] text-red-800 dark:text-red-200 flex items-start gap-1">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>{card.review_reason ?? "Review needed."}</span>
          </div>
        )}
        {!classified ? (
          <p className="text-muted-foreground">
            No source-authored explanation yet. Add a <code>probe-agent</code>{" "}
            docstring block or generate the capability hierarchy to classify this
            entrypoint. Graph and probe actions still work where a handler resolves.
          </p>
        ) : (
          <dl className="space-y-1.5">
            <Field label="Capability">
              {card.capability_name ?? card.capability_key}
            </Field>
            {card.element_type && (
              <Field label="Element type">{card.element_type}</Field>
            )}
            {card.role && <Field label="Role">{card.role}</Field>}
            {card.operation_kind && (
              <Field label="Operation kind">{card.operation_kind}</Field>
            )}
            {card.consumers.length > 0 && (
              <Field label="Consumers">{card.consumers.join(", ")}</Field>
            )}
            <Field label="State effects">
              {card.state_effects.length ? card.state_effects.join(", ") : "none"}
            </Field>
            <Field label="Boundaries">
              {card.boundaries.length ? card.boundaries.join(", ") : "none"}
            </Field>
            {card.probe_value && (
              <Field label="Probe value">{card.probe_value}</Field>
            )}
            {card.flows_through.length > 0 && (
              <Field label="Flows through">
                {card.flows_through.join(", ")}
              </Field>
            )}
          </dl>
        )}

        <div className="flex flex-wrap items-center gap-1 pt-1">
          <span className="text-muted-foreground text-[11px]">Provenance:</span>
          {(card.provenance_kinds.length ? card.provenance_kinds : ["unknown"]).map(p => (
            <Badge key={p} variant="outline" className={`text-[10px] ${PROVENANCE_STYLE[p] ?? ""}`}>
              {PROVENANCE_LABEL[p] ?? p}
            </Badge>
          ))}
        </div>

        {card.drift_status && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-muted-foreground text-[11px]">Freshness:</span>
            <Badge variant="outline" className={`text-[10px] ${DRIFT_STYLE[card.drift_status] ?? ""}`}>
              {DRIFT_LABEL[card.drift_status] ?? card.drift_status}
            </Badge>
            {card.drift_total_anchors > 0 && card.drift_changed_anchors > 0 && (
              <span className="text-[11px] text-muted-foreground">
                {card.drift_changed_anchors} of {card.drift_total_anchors} source
                anchors changed
              </span>
            )}
          </div>
        )}
        {!card.handler_resolved && (
          <p className="text-[11px] text-amber-700 dark:text-amber-300">
            No resolved handler — executable flow graph is not supported for this
            entrypoint.
          </p>
        )}

        {card.drift_review_recommended && (
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
              {refresh.isPending ? "Proposing..." : "Propose explanation refresh"}
            </Button>
            {proposal && <RefreshProposalPanel data={proposal} />}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
