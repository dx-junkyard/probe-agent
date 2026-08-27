import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import type {
  SnapshotFreshnessState,
  SnapshotPreflightCheckStatus,
  SnapshotPreflightOut,
} from "@/api/types";

// Issue #369: `ready` (the analysis finished) and `current` (the pinned commit
// still equals HEAD) are two independent axes. The audited bug was one word
// covering both — the Repository page warned that a snapshot was behind HEAD
// while labelling a different, older snapshot 「ready」, and Experiments
// offered every stale snapshot as an ordinary 「snapshot ready」 option.
//
// Everything below renders the server's evaluation (`GET /snapshot-preflight`).
// Nothing here re-derives a verdict, a freshness value, or a check status.

export const FRESHNESS_LABELS: Record<SnapshotFreshnessState, string> = {
  current: "最新（HEAD一致）",
  stale: "HEADより古い",
  unknown: "鮮度不明",
};

const FRESHNESS_VARIANTS: Record<
  SnapshotFreshnessState,
  "success" | "warning" | "secondary"
> = {
  current: "success",
  // `unknown` is not `stale`: an unreadable HEAD is not evidence that the
  // snapshot is behind, so it must not carry the warning colour.
  stale: "warning",
  unknown: "secondary",
};

const PROCESSING_LABELS: Record<string, string> = {
  ready: "解析完了",
  pending: "解析待ち",
  running: "解析中",
  failed: "解析失敗",
  partial: "一部のみ解析",
};

const CHECK_VARIANTS: Record<
  SnapshotPreflightCheckStatus,
  "success" | "warning" | "destructive" | "secondary"
> = {
  ok: "success",
  attention: "warning",
  blocking: "destructive",
  unknown: "secondary",
};

const VERDICT_LABELS: Record<string, string> = {
  ready: "開始できます",
  attention: "確認のうえ開始できます",
  blocked: "このSnapshotでは開始できません",
};

export function SnapshotFreshnessBadge({
  freshness,
}: {
  freshness: SnapshotFreshnessState;
}) {
  return (
    <Badge variant={FRESHNESS_VARIANTS[freshness]} data-testid="snapshot-freshness-badge">
      {FRESHNESS_LABELS[freshness]}
    </Badge>
  );
}

/** The processing axis, always labelled so it cannot be read as freshness. */
export function SnapshotProcessingBadge({ state }: { state: string | null }) {
  if (!state) return null;
  return (
    <Badge
      variant={state === "ready" ? "secondary" : "outline"}
      data-testid="snapshot-processing-badge"
    >
      {PROCESSING_LABELS[state] ?? state}
    </Badge>
  );
}

interface SnapshotPreflightPanelProps {
  preflight: SnapshotPreflightOut | undefined;
  isLoading?: boolean;
  /** Reason for continuing on a stale snapshot; controlled by the caller. */
  staleReason?: string;
  onStaleReasonChange?: (reason: string) => void;
}

export function SnapshotPreflightPanel({
  preflight,
  isLoading = false,
  staleReason = "",
  onStaleReasonChange,
}: SnapshotPreflightPanelProps) {
  if (isLoading && !preflight) {
    return (
      <div className="rounded-lg border p-3 text-xs text-muted-foreground" data-testid="snapshot-preflight-loading">
        Snapshotの状態を確認しています…
      </div>
    );
  }
  if (!preflight) return null;

  return (
    <div className="space-y-2 rounded-lg border p-3" data-testid="snapshot-preflight">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium">開始前の確認</span>
        <Badge
          variant={
            preflight.verdict === "ready"
              ? "success"
              : preflight.verdict === "attention"
                ? "warning"
                : "destructive"
          }
          data-testid="snapshot-preflight-verdict"
        >
          {VERDICT_LABELS[preflight.verdict] ?? preflight.verdict}
        </Badge>
        {/* Both axes, side by side and separately labelled. */}
        <SnapshotProcessingBadge state={preflight.processing_state} />
        <SnapshotFreshnessBadge freshness={preflight.freshness} />
        {preflight.snapshot_id != null && (
          <span className="text-xs text-muted-foreground">
            Snapshot #{preflight.snapshot_id}
            {preflight.commit_sha && (
              <> · <code className="font-mono">{preflight.commit_sha.slice(0, 8)}</code></>
            )}
          </span>
        )}
      </div>

      {!preflight.is_recommended && preflight.recommended_snapshot_id != null && (
        <p className="text-xs text-muted-foreground" data-testid="snapshot-not-recommended">
          推奨は Snapshot #{preflight.recommended_snapshot_id}
          {preflight.recommended_snapshot_commit_sha && (
            <> (<code className="font-mono">{preflight.recommended_snapshot_commit_sha.slice(0, 8)}</code>)</>
          )}
          です。この Snapshot は再現用途の選択です。
        </p>
      )}

      <ul className="space-y-1">
        {preflight.checks.map(check => (
          <li key={check.check_id} className="flex items-start gap-2 text-xs">
            <Badge variant={CHECK_VARIANTS[check.status]} className="shrink-0">
              {check.status}
            </Badge>
            <span className="min-w-0">
              <span className="font-medium">{check.summary}</span>
              <span className="text-muted-foreground"> — {check.detail}</span>
              {check.status !== "ok" && check.remediation && (
                <span className="block text-muted-foreground">{check.remediation}</span>
              )}
            </span>
          </li>
        ))}
      </ul>

      {/* The stale-continuation decision is a `decision_method: manual` fact:
          the developer states why an older snapshot is intended, and that
          reason is persisted on the record that consumes the snapshot. */}
      {preflight.requires_stale_acknowledgement && onStaleReasonChange && (
        <div className="space-y-1" data-testid="snapshot-stale-ack">
          {preflight.stale_continuation_note && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              {preflight.stale_continuation_note}
            </p>
          )}
          <Label className="text-xs" htmlFor="stale-ack-reason">
            この Snapshot を使う理由（必須）
          </Label>
          <Textarea
            id="stale-ack-reason"
            rows={2}
            value={staleReason}
            placeholder="例: 過去のバグを当時のコードで再現するため"
            onChange={e => onStaleReasonChange(e.target.value)}
          />
        </div>
      )}
    </div>
  );
}
