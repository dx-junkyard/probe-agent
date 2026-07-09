import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { useRepositoryStatus, useSystemUnderstanding } from "@/api/hooks";

// Issue #178: a read-only strip of "what am I looking at" shown on the main
// intelligence pages. Every value comes from data already fetched elsewhere
// (System list, Repository status, System Understanding summary) or from the
// URL query params the pages already use (`capability`, `entrypoint_type`,
// `entrypoint_id`) — no new state, no new API endpoint. Missing data just
// omits the row instead of showing an error, so a failed fetch never blocks
// the page it sits on top of.
const STEP_STATUS_LABELS: Record<string, string> = {
  repository_configured: "repository configured",
  snapshot_ready: "snapshot ready",
  documentation_indexed: "documentation indexed",
  documentation_claims_scanned: "documentation claims scanned",
  symbols_indexed: "symbols indexed",
  entrypoints_discovered: "entrypoints discovered",
  docs_code_reconciled: "docs-code reconciled",
  capability_hierarchy_ready: "capability hierarchy ready",
};

export function ContextHeader() {
  const { systems, systemId } = useAuth();
  const [searchParams] = useSearchParams();
  const { data: repoStatus } = useRepositoryStatus();
  const { data: understanding } = useSystemUnderstanding();

  const systemName = systems.find((s) => s.id === systemId)?.name ?? null;
  const commitSha = repoStatus?.current_head ?? repoStatus?.latest_snapshot?.commit_sha ?? null;
  const capability = searchParams.get("capability");
  const entrypointType = searchParams.get("entrypoint_type");
  const entrypointId = searchParams.get("entrypoint_id");
  const entrypoint = entrypointType && entrypointId ? `${entrypointType}: ${entrypointId}` : null;

  const statusParts: string[] = [];
  for (const step of understanding?.pipeline ?? []) {
    if (step.status === "complete") statusParts.push(STEP_STATUS_LABELS[step.step] ?? step.step);
  }
  const gapCount = understanding?.gaps.length ?? 0;
  if (gapCount > 0) statusParts.push(`${gapCount} gap${gapCount === 1 ? "" : "s"}`);
  const status = statusParts.length > 0 ? statusParts.join(", ") : null;

  const items: { key: string; label: string; value: string }[] = [
    { key: "system", label: "System", value: systemName ?? "" },
    { key: "snapshot", label: "Snapshot", value: commitSha ? commitSha.slice(0, 8) : "" },
    { key: "capability", label: "Capability", value: capability ?? "" },
    { key: "entrypoint", label: "Entrypoint", value: entrypoint ?? "" },
    { key: "status", label: "Status", value: status ?? "" },
  ].filter((item) => item.value !== "");

  if (items.length === 0) return null;

  return (
    <div
      data-testid="context-header"
      className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
    >
      {items.map((item) => (
        <span key={item.key} className="flex items-center gap-1 min-w-0" data-testid={`context-header-${item.key}`}>
          <span className="font-medium text-foreground shrink-0">{item.label}:</span>
          <span className="font-mono truncate max-w-[280px]">{item.value}</span>
        </span>
      ))}
    </div>
  );
}
