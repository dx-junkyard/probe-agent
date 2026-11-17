import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Ban, CircleAlert, RadioTower, XCircle } from "lucide-react";
import { api, getSystemId } from "@/api/client";
import { sysKey, useConnectivityStatus, useSystemState } from "@/api/hooks";
import { systemStateTarget } from "@/components/system-state";
import { targetHref } from "@/components/overview/display";
import type { OverviewActionOut, OverviewOut, SystemStateItem, UserPhase } from "@/api/types";

// Connectivity warning badge (Issues #165, #370).
//
// Shows an observation-based warning while the selected system is not
// currently receiving traces. The wording never claims "未設定" — the server
// only knows that nothing has been observed, not why.
//
// Issue #370: the badge reads `freshness` (the live axis), not `state` (the
// cumulative "has ever connected" milestone). A single trace from two weeks
// ago used to hide this badge permanently; a system that has stopped
// reporting is exactly the case it needs to surface.
type BadgeSpec = { label: string; title: string; tone: "amber" | "blue" | "red" };

const TONE_CLASSES: Record<BadgeSpec["tone"], string> = {
  amber:
    "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200",
  blue:
    "border-blue-300 bg-blue-50 text-blue-800 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-200",
  red:
    "border-red-300 bg-red-50 text-red-800 hover:bg-red-100 dark:border-red-800 dark:bg-red-950/30 dark:text-red-200",
};

// UX audit 2026-11-17 A3: phases in which SDK connection has not been
// reached yet. Before instrumentation, 「未受信」 is an expected,
// not-yet-reached fact, not a warning -- and showing it as one made the
// header's only text notice point at the wrong cause while the real blocker
// (repository root, reasoning model, ...) sat inside the shield icon. The
// phase value is the server's `user_phase`; this is only a fixed subset of
// the server's finite PHASE_ORDER, never a client-side phase derivation.
const PRE_CONNECTION_PHASES: ReadonlySet<UserPhase> = new Set<UserPhase>(["setup", "preparation"]);

const NOTICE_TONE: Record<SystemStateItem["severity"], BadgeSpec["tone"]> = {
  error: "red",
  blocked: "amber",
  warning: "amber",
  info: "blue",
  ok: "blue",
};

function NoticeIcon({ severity }: { severity: SystemStateItem["severity"] }) {
  const className = "h-3.5 w-3.5 shrink-0";
  if (severity === "error") return <XCircle className={className} />;
  if (severity === "blocked") return <Ban className={className} />;
  if (severity === "warning") return <AlertTriangle className={className} />;
  return <CircleAlert className={className} />;
}

/** The server's highest-priority notification, as header text.
 *
 * Copy, severity and destination all come verbatim from the canonical
 * `StateItem` (`notification_items[0]`, falling back to `primary_item`);
 * nothing is re-derived here (Issue #239). */
function PrimaryNoticeChip({ item }: { item: SystemStateItem }) {
  const target = systemStateTarget(item);
  const className = `inline-flex min-w-0 max-w-72 shrink items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-medium transition-colors @4xl:px-2.5 ${TONE_CLASSES[NOTICE_TONE[item.severity] ?? "amber"]}`;
  const title = item.remediation ? `${item.summary} — ${item.remediation}` : item.summary;
  const body = (
    <>
      <NoticeIcon severity={item.severity} />
      <span className="hidden truncate @4xl:inline">{item.summary}</span>
    </>
  );
  return target ? (
    <Link
      to={target}
      data-testid="header-primary-notice"
      data-state-id={item.state_id}
      title={title}
      aria-label={item.summary}
      className={className}
    >
      {body}
    </Link>
  ) : (
    <span
      data-testid="header-primary-notice"
      data-state-id={item.state_id}
      title={title}
      aria-label={item.summary}
      className={className}
    >
      {body}
    </span>
  );
}

/** The Overview's server-decided next step, as header text.
 *
 * Label, reason and destination are `OverviewOut.next_action` verbatim (the
 * first-match table in `app/overview_projection.py`); the header never picks
 * between candidates or composes a step of its own. */
function NextActionChip({ action }: { action: OverviewActionOut }) {
  return (
    <Link
      to={targetHref(action.target)}
      data-testid="header-primary-notice"
      data-action-key={action.key}
      title={action.reason ? `${action.label} — ${action.reason}` : action.label}
      aria-label={`次にすること: ${action.label}`}
      className={`inline-flex min-w-0 max-w-72 shrink items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-medium transition-colors @4xl:px-2.5 ${TONE_CLASSES.blue}`}
    >
      <ArrowRight className="h-3.5 w-3.5 shrink-0" />
      <span className="hidden truncate @4xl:inline">{action.label}</span>
    </Link>
  );
}

/** What the header's one text slot currently shows.
 *
 * `coveredKeys` holds the `state_id` / `dedupe_key` values of every canonical
 * `StateItem` the header text already names, so another notification surface
 * (the assistant's floating notice) can avoid repeating the same cause. */
export type HeaderTextNotice =
  | { kind: "pending"; coveredKeys: ReadonlySet<string> }
  | { kind: "none"; coveredKeys: ReadonlySet<string> }
  | { kind: "next_action"; action: OverviewActionOut; coveredKeys: ReadonlySet<string> }
  | { kind: "state_item"; item: SystemStateItem; coveredKeys: ReadonlySet<string> }
  | { kind: "connectivity"; coveredKeys: ReadonlySet<string> };

const NOTHING_COVERED: ReadonlySet<string> = new Set<string>();

function itemKeys(item: SystemStateItem): string[] {
  return item.dedupe_key ? [item.state_id, item.dedupe_key] : [item.state_id];
}

/**
 * The header's one text notice, decided once and shared by every surface that
 * must agree with it.
 *
 * - Before SDK connection is reached (`user_phase` is setup/preparation) and
 *   no real workload has ever been received, the slot shows the Overview's
 *   next step (`GET /overview` `next_action`) instead of a connectivity
 *   warning -- the cause that actually blocks progress, and the same one the
 *   Overview names (UX audit 2026-11-17 A3). `notification_items` is ordered
 *   by severity, not by the setup's causal order, so its first entry can name
 *   a different cause (e.g. the reasoning model while no repository exists);
 *   the header therefore does not read it while the Overview has an answer.
 *   When the Overview has no action (loading is `pending`; waiting,
 *   unavailable, or a failed read) it falls back to the server's
 *   highest-priority notification. Nothing is ranked client-side.
 * - Otherwise (instrumentation or later, a system that has received real
 *   workload before, a server without `user_phase`, or a failed system-state
 *   read) the #370 behaviour is unchanged: the headline is `freshness`, so a
 *   system that connected and then went quiet is still warned about.
 */
export function useHeaderTextNotice(): HeaderTextNotice {
  const { data } = useConnectivityStatus();
  const { data: systemState, isLoading: stateLoading } = useSystemState();
  const neverReceived = !data || data.freshness === "never_received";
  const preConnection =
    !!systemState?.user_phase && PRE_CONNECTION_PHASES.has(systemState.user_phase);
  const overviewQuery = useQuery({
    queryKey: sysKey("overview"),
    queryFn: () => api.get<OverviewOut>("/overview"),
    enabled: preConnection && neverReceived && !!getSystemId(),
  });

  // Until the phase is known, a never-received reading cannot yet be told
  // apart from an expected not-yet-reached one; render nothing rather than
  // flash a warning that the phase may withdraw a moment later.
  if (stateLoading && neverReceived) return { kind: "pending", coveredKeys: NOTHING_COVERED };

  if (preConnection && neverReceived) {
    if (overviewQuery.isLoading) return { kind: "pending", coveredKeys: NOTHING_COVERED };
    const action = overviewQuery.data?.next_action ?? null;
    if (action) {
      const sourceIds = new Set(action.source_state_ids);
      const keys = new Set<string>(action.source_state_ids);
      const candidates = [
        ...(systemState?.notification_items ?? []),
        ...(systemState?.primary_item ? [systemState.primary_item] : []),
      ];
      for (const item of candidates) {
        if (sourceIds.has(item.state_id)) itemKeys(item).forEach((k) => keys.add(k));
      }
      return { kind: "next_action", action, coveredKeys: keys };
    }
    const item = systemState?.notification_items[0] ?? systemState?.primary_item ?? null;
    return item
      ? { kind: "state_item", item, coveredKeys: new Set(itemKeys(item)) }
      : { kind: "none", coveredKeys: NOTHING_COVERED };
  }

  if (!data || data.freshness === "receiving_now") return { kind: "none", coveredKeys: NOTHING_COVERED };
  return { kind: "connectivity", coveredKeys: NOTHING_COVERED };
}

/** True when the header's text notice already names this item's cause. */
export function headerCoversItem(notice: HeaderTextNotice, item: SystemStateItem | null | undefined): boolean {
  if (!item) return false;
  return itemKeys(item).some((k) => notice.coveredKeys.has(k));
}

export function ConnectivityBadge() {
  const { data } = useConnectivityStatus();
  const notice = useHeaderTextNotice();

  if (notice.kind === "next_action") return <NextActionChip action={notice.action} />;
  if (notice.kind === "state_item") return <PrimaryNoticeChip item={notice.item} />;
  if (notice.kind !== "connectivity" || !data) return null;

  let spec: BadgeSpec;
  if (data.freshness === "never_received") {
    spec = data.state === "smoke_only"
      ? {
          label: "疎通確認のみ受信",
          title:
            "手動の疎通確認 trace のみ受信しています。実 workload からの trace はまだ届いていません。",
          tone: "blue",
        }
      : {
          label: "シグナル未受信 — 疎通確認が必要",
          title:
            "このシステムはまだ trace / signal を一度も受信していません。クリックで接続ガイドを開きます。",
          tone: "amber",
        };
  } else if (data.freshness === "delayed") {
    spec = {
      label: "受信が途絶えています",
      title:
        "過去には受信していますが、直近の trace が届いていません。クリックで確認手順を開きます。",
      tone: "amber",
    };
  } else {
    spec = {
      label: "長時間受信なし",
      title:
        "長時間 trace が届いていません。health / 認証 / network / workload を確認してください。",
      tone: "red",
    };
  }

  return (
    <Link
      to="/setup-guide"
      data-testid="connectivity-badge"
      data-state={data.state}
      data-freshness={data.freshness}
      title={spec.title}
      aria-label={spec.label}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-medium transition-colors @4xl:px-2.5 ${TONE_CLASSES[spec.tone]}`}
    >
      <RadioTower className="h-3.5 w-3.5" />
      {/* Issue #466 (UX-14): 狭い画面では文言を畳み、アイコン + アクセシブル名
          (と title) だけで通知を残す。重要通知は隠さない。 */}
      <span className="hidden @4xl:inline">{spec.label}</span>
    </Link>
  );
}
