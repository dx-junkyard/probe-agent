// Per-field conflict preview for a prefilled form draft (Issue #446, Epic
// #443 Phase 3). docs/ai-discussion-adapter.md §3.3.
//
// Shared by every receiving form (`RequirementRevisionForm`,
// `JourneyRevisionForm`, `AddOptionForm`) so the conflict UI reads the same
// way everywhere: a dirty field whose current value differs from the
// proposed one shows both values side by side and two explicit choices,
// 「このまま」 (keep the developer's typed value) and 「提案で置き換える」
// (overwrite it with the proposal's value). A field the developer does not
// choose to replace keeps its typed value untouched.

import { Button } from "@/components/ui/button";
import type { FieldConflict, FieldResolution } from "@/lib/form-draft-inbox";

export function FormDraftConflictBanner({
  conflicts,
  onResolve,
}: {
  conflicts: FieldConflict[];
  onResolve: (fieldName: string, resolution: FieldResolution) => void;
}) {
  if (conflicts.length === 0) return null;
  return (
    <div
      className="space-y-2 rounded border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
      role="status"
      data-testid="form-draft-conflict-banner"
    >
      <p className="text-xs font-medium">
        AI からの変更候補と、いま入力中の内容が異なります。項目ごとに選んでください。
      </p>
      {conflicts.map((c) => (
        <div
          key={c.fieldName}
          className="space-y-1 rounded border bg-background p-2 text-xs"
          data-testid={`form-draft-conflict-${c.fieldName}`}
        >
          <p className="font-mono font-medium">{c.fieldName}</p>
          <p className="text-muted-foreground">
            いまの内容: <span className="text-foreground">{c.currentValue || "(空)"}</span>
          </p>
          <p className="text-muted-foreground">
            提案: <span className="text-foreground">{c.proposedValue || "(空)"}</span>
          </p>
          {c.rationale && <p className="text-muted-foreground">理由: {c.rationale}</p>}
          <div className="flex gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              onClick={() => onResolve(c.fieldName, "keep")}
              data-testid={`form-draft-conflict-keep-${c.fieldName}`}
            >
              このまま
            </Button>
            <Button
              size="sm"
              variant="default"
              onClick={() => onResolve(c.fieldName, "replace")}
              data-testid={`form-draft-conflict-replace-${c.fieldName}`}
            >
              提案で置き換える
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
