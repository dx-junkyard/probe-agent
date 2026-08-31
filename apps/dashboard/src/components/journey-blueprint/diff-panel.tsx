// Issue #423 (Epic #418): §8.3's as-is/to-be diff panel. Grouped by the
// server's own `change_kind` (`diffChangeGroups`, `model.ts`) -- this
// component performs no matching or classification of its own.

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BlueprintDiffOut } from "@/api/types";
import { BLUEPRINT_DIFF_STATE_LABEL, diffChangeGroups } from "./model";

export function BlueprintDiffPanel({ diff }: { diff: BlueprintDiffOut | null | undefined }) {
  if (!diff) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="blueprint-diff-loading">
        読み込み中です。
      </p>
    );
  }

  if (diff.diff_state !== "available") {
    return (
      <p className="text-sm text-muted-foreground" data-testid="blueprint-diff-not-available">
        {BLUEPRINT_DIFF_STATE_LABEL[diff.diff_state] ?? diff.diff_state}
      </p>
    );
  }

  const groups = diffChangeGroups(diff);

  return (
    <Card data-testid="blueprint-diff-panel" data-help-id="journey-blueprint.diff">
      <CardHeader>
        <CardTitle as="h2" className="text-base">
          現状 (as-is) と目標 (to-be) の Step 差分
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {groups.map((group) => (
          <div key={group.changeKind} data-testid={`blueprint-diff-group-${group.changeKind}`}>
            <p className="text-sm font-medium">
              {group.label}
              <Badge variant="secondary" className="ml-2">
                {group.entries.length} 件
              </Badge>
            </p>
            {group.entries.length > 0 ? (
              <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                {group.entries.map((entry) => (
                  <li key={entry.step_key} data-testid="blueprint-diff-entry">
                    {entry.step_key}
                    {entry.change_kind === "reordered"
                      ? ` (${entry.from_step_order} -> ${entry.to_step_order})`
                      : null}
                    {entry.change_kind === "changed" ? ` : ${entry.from_user_intent} -> ${entry.to_user_intent}` : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
