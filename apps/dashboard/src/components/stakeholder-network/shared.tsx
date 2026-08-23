// Issue #422: small display primitives for the Stakeholder Value Network
// screen only -- deliberately NOT imported from `components/ux-design/
// shared.tsx` (a file that Epic owns), even though the shapes are similar,
// so this Epic's exclusive file list stays exact.

import { type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

/** 「取得できませんでした」 -- a failed query's card, never an empty page body. */
export function LoadErrorCard({
  detail,
  onRetry,
}: {
  detail?: string;
  onRetry: () => void;
}) {
  return (
    <div
      className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm"
      data-testid="value-network-load-error"
      role="alert"
    >
      <p className="font-medium text-destructive">取得できませんでした</p>
      {detail && <p className="mt-1 text-muted-foreground">{detail}</p>}
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
        再試行
      </Button>
    </div>
  );
}

export function LoadingBlock() {
  return (
    <div data-testid="value-network-loading" className="space-y-2" aria-busy="true">
      <Skeleton className="h-6 w-full" />
      <Skeleton className="h-6 w-3/4" />
    </div>
  );
}

export function EmptyNote({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className="text-sm text-muted-foreground" data-testid={testId ?? "value-network-empty"}>
      {children}
    </p>
  );
}

/** Pairs a colour with TEXT -- #358's "never colour alone" rule. `label` is
 * always the caller's own fixed copy from `model.ts`. */
export function StateBadge({
  label,
  tone = "outline",
}: {
  label: string;
  tone?: "outline" | "secondary" | "success" | "warning" | "destructive";
}) {
  return <Badge variant={tone}>{label}</Badge>;
}

/** Names WHICH section could not be read -- never substitutes a blank or a
 * zero for it (#380's discipline, applied here). */
export function DegradedNote({ sections, detail }: { sections: string[]; detail: Record<string, string> }) {
  if (sections.length === 0) return null;
  return (
    <div
      className="rounded border border-amber-400/50 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200"
      data-testid="value-network-degraded"
    >
      一部の情報を取得できませんでした: {sections.join(", ")}
      {sections.some((s) => detail[s]) && (
        <span> ({sections.filter((s) => detail[s]).map((s) => `${s}: ${detail[s]}`).join(" / ")})</span>
      )}
    </div>
  );
}
