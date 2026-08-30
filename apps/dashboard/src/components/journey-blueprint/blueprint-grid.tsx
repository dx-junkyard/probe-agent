// Issue #423 (Epic #418): the Service Blueprint grid -- Steps as columns,
// the nine lanes as rows. Renders the server's `BlueprintOut` verbatim; no
// lane state, digest, or link is recomputed here (§0 invariant 9).
//
// Narrow-width behaviour (§7.3/#358's rule applied one layer over): below
// ~360px a horizontally-scrolling grid stays reachable via the surrounding
// `overflow-x-auto` wrapper rather than hiding columns -- nothing is
// dropped, it degrades to a stacked, scrollable presentation.

import { Badge } from "@/components/ui/badge";
import type { BlueprintLaneCellOut, BlueprintOut } from "@/api/types";
import {
  BLUEPRINT_LANE_LABEL,
  BLUEPRINT_LANE_LEGEND,
  BLUEPRINT_LANE_ORDER,
  LANE_STATE_LABEL,
  LANE_STATE_NEEDS_ATTENTION,
  orderedSteps,
} from "./model";

function laneCellBadgeVariant(cell: BlueprintLaneCellOut | undefined): "secondary" | "outline" | "warning" {
  if (!cell) return "outline";
  if (cell.state === "present") return "secondary";
  if (LANE_STATE_NEEDS_ATTENTION[cell.state]) return "warning";
  return "outline";
}

export function BlueprintCell({
  cell,
  onSelect,
}: {
  cell: BlueprintLaneCellOut | undefined;
  onSelect?: (cell: BlueprintLaneCellOut) => void;
}) {
  if (!cell) {
    return (
      <div className="flex h-full min-h-[64px] flex-col gap-1 rounded-md border border-dashed p-2 text-xs text-muted-foreground">
        {LANE_STATE_LABEL.unavailable}
      </div>
    );
  }
  return (
    <button
      type="button"
      data-testid={`blueprint-cell-${cell.lane_kind}`}
      onClick={() => onSelect?.(cell)}
      className="flex h-full min-h-[64px] w-full flex-col items-start gap-1 rounded-md border p-2 text-left text-xs hover:bg-muted/50"
    >
      <Badge variant={laneCellBadgeVariant(cell)} data-testid={`blueprint-cell-state-${cell.lane_kind}`}>
        {LANE_STATE_LABEL[cell.state]}
      </Badge>
      {cell.summary ? <p className="line-clamp-2 text-muted-foreground">{cell.summary}</p> : null}
    </button>
  );
}

export function BlueprintGrid({
  blueprint,
  onSelectCell,
}: {
  blueprint: BlueprintOut;
  onSelectCell?: (stepKey: string, cell: BlueprintLaneCellOut) => void;
}) {
  const steps = orderedSteps(blueprint);

  if (steps.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="blueprint-grid-empty">
        この Journey にはまだ Step がありません。
      </p>
    );
  }

  return (
    <div className="overflow-x-auto" data-testid="blueprint-grid">
      <table className="w-full min-w-[720px] border-separate border-spacing-2">
        <thead>
          <tr>
            <th className="w-40 text-left text-xs font-medium text-muted-foreground">レーン</th>
            {steps.map((step) => (
              <th key={step.step_key} className="min-w-[180px] text-left text-xs font-medium">
                {step.step_key}
                <p className="font-normal text-muted-foreground">{step.user_intent}</p>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {BLUEPRINT_LANE_ORDER.map((laneKind) => (
            <tr key={laneKind}>
              <th
                scope="row"
                className="align-top text-left text-xs font-medium"
                data-help-id={`journey-blueprint.lane.${laneKind}`}
              >
                {BLUEPRINT_LANE_LABEL[laneKind]}
                <p className="font-normal text-muted-foreground">{BLUEPRINT_LANE_LEGEND[laneKind]}</p>
              </th>
              {steps.map((step) => (
                <td key={`${step.step_key}-${laneKind}`} className="align-top">
                  <BlueprintCell
                    cell={step.lanes[laneKind]}
                    onSelect={(cell) => onSelectCell?.(step.step_key, cell)}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
