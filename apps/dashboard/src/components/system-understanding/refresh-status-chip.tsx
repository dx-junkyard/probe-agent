// Issue #288: 回答バッチ後の自動更新 status chip.
//
// Shown near 現在の理解 / レビューキュー so the developer can see that an
// answer/決定 already triggered an automatic refresh, without needing to
// press 「理解を更新」 themselves. Server enum values (RefreshJobStatus) map
// to Japanese labels only here (Issue #266 UI言語規約) -- the canonical
// status string itself is never translated in API payloads/logs.

import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRefreshStatus, useRetryRefreshJob } from "@/api/hooks";
import type { RefreshJobStatus } from "@/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

const STATUS_LABELS: Record<RefreshJobStatus, string> = {
  pending: "更新待ち…",
  updating: "更新中…",
  updated: "更新済み",
  failed: "更新に失敗しました",
  stale: "古い結果を破棄しました",
};

const STATUS_VARIANTS: Record<RefreshJobStatus, BadgeVariant> = {
  pending: "secondary",
  updating: "secondary",
  updated: "success",
  failed: "destructive",
  stale: "warning",
};

export function RefreshStatusChip({ sessionId }: { sessionId: number | null }) {
  const { data: status } = useRefreshStatus(sessionId);
  const retry = useRetryRefreshJob(sessionId);
  const job = status?.latest_job;

  if (!job) return null;

  const handleRetry = () => {
    retry.mutate(
      { jobId: job.id },
      {
        onSuccess: () => toast.success("自動更新を再試行しています…"),
        onError: e => toast.error(String(e)),
      },
    );
  };

  // status='updated' でも error が残っている場合は部分失敗
  // (理解の再構築は成功、Alignment 更新のみ失敗)。成功として
  // 見せず、警告付きラベルで失敗が起きたことを短く示す。
  const partialFailure = job.status === "updated" && Boolean(job.error);

  return (
    <div className="flex items-center gap-1.5" data-testid="refresh-status-chip">
      <Badge
        variant={partialFailure ? "warning" : STATUS_VARIANTS[job.status]}
        className="flex items-center gap-1"
        title={partialFailure ? job.error ?? undefined : undefined}
      >
        {(job.status === "pending" || job.status === "updating") && (
          <Loader2 className="h-3 w-3 animate-spin" />
        )}
        {partialFailure ? "更新済み(一部失敗あり)" : STATUS_LABELS[job.status]}
      </Badge>
      {(job.status === "failed" || partialFailure) && (
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs"
          onClick={handleRetry}
          disabled={retry.isPending}
          data-testid="refresh-status-retry-button"
        >
          再試行
        </Button>
      )}
    </div>
  );
}
