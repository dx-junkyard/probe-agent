import { Button } from "@/components/ui/button";
import { describeRequestFailure } from "@/lib/request-state";
import { cn } from "@/lib/utils";

// Issue #466 (UX-05): 取得失敗の共通表示。読み込み中・取得成功 0 件・失敗は
// 別の表示で、失敗時の主操作は「再試行」。0 件用の文言 (「まだありません」)
// や作成 CTA をここに出してはならない — 取得に失敗した時点で、対象が本当に
// 無いのかは分からないため。

export function QueryErrorState({
  target,
  error,
  onRetry,
  retrying = false,
  className,
  "data-testid": testId,
}: {
  /** 何の取得に失敗したか (例: 「Probe plan 一覧」)。 */
  target: string;
  error: unknown;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
  "data-testid"?: string;
}) {
  const failure = describeRequestFailure(error);
  return (
    <div
      role="alert"
      data-testid={testId}
      data-failure-kind={failure.kind}
      className={cn("rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm space-y-2", className)}
    >
      <p className="font-medium">{target}を取得できませんでした。</p>
      <p className="text-muted-foreground">{failure.summary}</p>
      {failure.detail && (
        <p className="text-xs text-muted-foreground break-words">詳細: {failure.detail}</p>
      )}
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying}>
          {retrying ? "再試行中…" : "再試行"}
        </Button>
      )}
    </div>
  );
}
