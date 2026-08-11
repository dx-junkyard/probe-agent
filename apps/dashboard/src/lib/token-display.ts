import type { TokenOut, TokenStatus } from "@/api/types";

/**
 * Issue #368: presentation for `TokenOut.status`.
 *
 * The status itself is decided by the Control Server
 * (`app/token_status.py`) — nothing here re-derives it from `revoked` /
 * `expires_at`. This module only maps the finite server value onto the
 * Japanese label, the badge variant, and the ordering the SDK flow needs.
 */

export const TOKEN_STATUS_LABEL: Record<TokenStatus, string> = {
  active: "有効",
  expiring_soon: "まもなく期限切れ",
  expired: "期限切れ",
  revoked: "失効済み",
};

/**
 * `expired` and `revoked` must never be green: the whole bug in #368 was an
 * unusable token rendering as a healthy one. `expiring_soon` is amber (still
 * usable, needs action), the two unusable states are secondary/destructive.
 */
export const TOKEN_STATUS_VARIANT: Record<
  TokenStatus,
  "success" | "warning" | "secondary" | "destructive"
> = {
  active: "success",
  expiring_soon: "warning",
  expired: "secondary",
  revoked: "destructive",
};

/** Statuses whose token can still authenticate an SDK request right now. */
const USABLE_STATUSES: readonly TokenStatus[] = ["active", "expiring_soon"];

export function isTokenUsable(status: TokenStatus | undefined): boolean {
  return status !== undefined && USABLE_STATUSES.includes(status);
}

/** Display priority: usable first, then expired, then revoked. */
const STATUS_RANK: Record<TokenStatus, number> = {
  active: 0,
  expiring_soon: 1,
  expired: 2,
  revoked: 3,
};

/**
 * 「有効な API token を優先」 — order by the finite status rank, then newest
 * first. Pure and total: it never mutates its input.
 */
export function sortTokensByUsability<T extends { status: TokenStatus; created_at: unknown }>(
  tokens: readonly T[],
): T[] {
  return [...tokens].sort((a, b) => {
    const rank = (STATUS_RANK[a.status] ?? 99) - (STATUS_RANK[b.status] ?? 99);
    if (rank !== 0) return rank;
    return Number(b.created_at ?? 0) - Number(a.created_at ?? 0);
  });
}

/**
 * Relative time to expiry, from the server-computed `expires_in_seconds` so
 * the wording can never contradict the badge next to it (they were decided at
 * the same instant, on the same clock).
 */
export function formatExpiresIn(token: Pick<TokenOut, "status" | "expires_in_seconds" | "expires_at">): string {
  if (token.expires_at == null) return "無期限";
  if (token.status === "expired") return "期限切れ";
  const seconds = token.expires_in_seconds;
  if (seconds == null) return "—";
  if (seconds <= 0) return "期限切れ";
  if (seconds < 60) return "あと1分未満";
  if (seconds < 3600) return `あと約${Math.floor(seconds / 60)}分`;
  if (seconds < 86400) return `あと約${Math.floor(seconds / 3600)}時間`;
  return `あと約${Math.floor(seconds / 86400)}日`;
}

/** SDK credentials are `kind === "api"`; `session` rows are login sessions. */
export function isApiToken(token: Pick<TokenOut, "kind">): boolean {
  return token.kind === "api";
}
