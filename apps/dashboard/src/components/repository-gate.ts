import { useRepositoryStatus, useSystemState } from "@/api/hooks";
import { systemStateTarget } from "@/components/system-state";

/**
 * Issue #258: a deterministic "prerequisite unmet" result -- disabled +
 * reason + a link to the page that satisfies the prerequisite. `blocked` is
 * only ever true once the underlying fact is definitively known; every
 * caller must treat `blocked === false` as "do not disable" (the two-stage
 * escape hatch: an unknown/loading/errored state never blocks an action,
 * only warns).
 */
export interface RepositoryGate {
  blocked: boolean;
  summary: string | null;
  remediation: string | null;
  to: string | null;
  actionLabel: string | null;
}

const UNBLOCKED: RepositoryGate = {
  blocked: false, summary: null, remediation: null, to: null, actionLabel: null,
};

/**
 * "Repository is configured" prerequisite, used by actions that only need a
 * repository path (e.g. Repository's own Create Snapshot -- gating that
 * button on "no ready snapshot" would be a chicken-and-egg deadlock, since
 * creating the first snapshot is the fix).
 *
 * Two-stage rule: while useRepositoryStatus() is loading/erroring the fact
 * is unknown, so this returns UNBLOCKED rather than guessing; only a
 * definitive `configured === false` blocks.
 *
 * Reason copy prefers the #240 message-catalog StateItem
 * ("repository.configuration.missing", the same item already driving the
 * Repository page's SystemStateBanner) via `page_items["/repository"]`, so
 * the button's reason and the page banner never disagree. Falls back to
 * inline copy only if an older server response omits that item.
 */
export function useRepositoryConfiguredGate(): RepositoryGate {
  const { data: status, isLoading, isError } = useRepositoryStatus();
  const { data: systemState } = useSystemState();

  if (isLoading || isError || !status) return UNBLOCKED;
  if (status.configured) return UNBLOCKED;

  const item = systemState?.page_items["/repository"]?.find(
    (i) => i.state_id === "repository.configuration.missing",
  );
  if (item) {
    return {
      blocked: true,
      summary: item.summary,
      remediation: item.remediation || null,
      to: systemStateTarget(item),
      actionLabel: item.target_ui?.action_label ?? null,
    };
  }
  return {
    blocked: true,
    summary: "No target repository is configured for this System.",
    remediation: "Configure the target repository in the Repository tab.",
    to: "/repository",
    actionLabel: "Go to Repository",
  };
}

/**
 * "Repository is configured AND its latest snapshot is ready" prerequisite,
 * used by actions whose server endpoint rejects on exactly that fact --
 * mirrors `generate_probe_plan_endpoint` / patch generation's own check
 * (`snapshot_row is None or snapshot_row["status"] != "ready"`, evaluated
 * against the latest snapshot) using the already-fetched
 * useRepositoryStatus() fields. No new server calls.
 *
 * There is no dedicated #240 StateItem for "configured but zero ready
 * snapshots yet" (`repository.snapshot.stale` only covers a HEAD-changed-
 * since-a-ready-snapshot condition, a different fact), so that branch always
 * uses inline copy + a Link to /repository, per the issue's fallback rule.
 */
export function useRepositoryReadySnapshotGate(): RepositoryGate {
  const configuredGate = useRepositoryConfiguredGate();
  const { data: status, isLoading, isError } = useRepositoryStatus();

  if (configuredGate.blocked) return configuredGate;
  if (isLoading || isError || !status) return UNBLOCKED;

  if (!status.latest_snapshot || status.latest_snapshot.status !== "ready") {
    return {
      blocked: true,
      summary: "No ready repository snapshot exists yet.",
      remediation: "Create a snapshot in the Repository tab before continuing.",
      to: "/repository",
      actionLabel: "Go to Repository",
    };
  }
  return UNBLOCKED;
}
