import type { ConnectivityStatusOut, TokenOut } from "@/api/types";
import { isApiToken, isTokenUsable } from "@/lib/token-display";

// Issue #374: the Setup Guide put connection state, an 8-step explanation,
// patch application, 4 runtime patterns, auth, health, smoke, workload,
// troubleshooting, and an environment-variable reference on one page, so the
// operation actually needed right now was buried.
//
// This module answers three questions, deterministically, from persisted
// facts already on the page: where am I, what is the single next operation,
// and how do I know it is done. First-match over a finite step list — no
// scoring, no inference about *why* something has not happened.

export const SETUP_STEPS = [
  "issue_token",
  "configure_sdk",
  "verify_transport",
  "run_workload",
  "restore_reception",
] as const;

export type SetupStepId = (typeof SETUP_STEPS)[number];

export interface SetupStep {
  id: SetupStepId;
  /** Where the developer stands. */
  state: string;
  /** Exactly one operation. */
  action: string;
  /** How they will know it worked. */
  completion: string;
}

const STEPS: Record<SetupStepId, SetupStep> = {
  issue_token: {
    id: "issue_token",
    state: "SDK 用の API token がまだありません",
    action: "Connect SDK で API token を発行する",
    completion: "有効な API token が一覧に表示される",
  },
  configure_sdk: {
    id: "configure_sdk",
    state: "token はありますが、まだ何も受信していません",
    action: "監視対象に SDK を導入し、PROBE_SERVER_URL と token を設定する",
    completion: "手動 smoke trace が HTTP 201 を返す",
  },
  verify_transport: {
    id: "verify_transport",
    state: "疎通確認 trace のみ受信しています（受信経路は正常）",
    action: "@probe を付けたアプリを実際に動かす",
    completion: "実 workload の trace が届き、Components に表示される",
  },
  run_workload: {
    id: "run_workload",
    state: "実 workload の trace を受信しています",
    action: "Components で trace を確認する",
    completion: "改善したい component の入出力が読める",
  },
  restore_reception: {
    id: "restore_reception",
    state: "過去には受信していましたが、現在は届いていません",
    action: "health・認証・network・workload の順に確認する",
    completion: "直近の期間別件数が 0 でなくなる",
  },
};

/**
 * First-match resolution over persisted facts.
 *
 * Deliberately reads Issue #370's `freshness` (the live axis) rather than
 * `state` (the cumulative milestone): a system that once connected and has
 * since gone silent needs the recovery step, not a completion message.
 */
export function resolveSetupStep(
  connectivity: ConnectivityStatusOut | undefined,
  tokens: TokenOut[] | undefined,
  systemId: number | null,
): SetupStep {
  const hasUsableToken = (tokens ?? []).some(
    t =>
      isApiToken(t) &&
      isTokenUsable(t.status) &&
      (t.system_id == null || t.system_id === systemId),
  );

  if (!connectivity) return STEPS.configure_sdk;

  if (connectivity.freshness === "never_received") {
    if (!hasUsableToken) return STEPS.issue_token;
    return connectivity.state === "smoke_only"
      ? STEPS.verify_transport
      : STEPS.configure_sdk;
  }
  if (connectivity.freshness === "delayed" || connectivity.freshness === "stale") {
    return STEPS.restore_reception;
  }
  // receiving_now: a smoke trace proves the transport, not the workload.
  return connectivity.state === "smoke_only" ? STEPS.verify_transport : STEPS.run_workload;
}
