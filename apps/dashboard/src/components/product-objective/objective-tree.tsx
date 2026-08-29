// Issue #432 (Epic #427): the Objective Map lane -- a progressively
// disclosed Objective/Milestone tree (§9.5: "全ツリーを常時展開しない").
//
// Every state rendered here (`objective_state`, `recheck_state`,
// `design_status`, `achievement`, `assessability`, Gap counts) arrives
// already decided by `GET /objective-map`; this file only decides expand/
// collapse and which node is selected (`components/product-objective/model.ts`
// resolves ids to nodes, this component owns no state logic beyond that).

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { ObjectiveMapNodeOut, ObjectiveMapOut } from "@/api/types";
import {
  DESIGN_STATUS_LABEL, GAP_SUMMARY_UNAVAILABLE_LABEL, MILESTONE_ACHIEVEMENT_LABEL,
  MILESTONE_ASSESSABILITY_LABEL, OBJECTIVE_STATE_LABEL, RECHECK_STATE_LABEL,
  isMilestoneGapSummaryUnavailable, objectiveGapTotal, objectiveMapChildren,
  objectiveMapForceExpandedKeys, objectiveMapRoots,
} from "./model";

function RecheckMarker({ state }: { state: "current" | "stale" | "not_captured" }) {
  if (state === "current") return null;
  // Never colour alone -- a short text marker travels with the badge.
  return (
    <Badge variant={state === "stale" ? "warning" : "outline"} data-testid="recheck-marker">
      {RECHECK_STATE_LABEL[state]}
    </Badge>
  );
}

function ObjectiveNode({
  map,
  node,
  depth,
  selectedObjectiveKey,
  selectedMilestoneKey,
  expandedKeys,
  onToggle,
  onSelectObjective,
  onSelectMilestone,
}: {
  map: ObjectiveMapOut;
  node: ObjectiveMapNodeOut;
  depth: number;
  selectedObjectiveKey: string | null;
  selectedMilestoneKey: string | null;
  expandedKeys: Set<string>;
  onToggle: (key: string) => void;
  onSelectObjective: (key: string) => void;
  onSelectMilestone: (key: string) => void;
}) {
  const isSelected = node.objective_key === selectedObjectiveKey;
  // Progressive disclosure: a node's expand/collapse state is owned by
  // `ObjectiveTree` (a Set of objective_key), not local component state, so
  // a deep link can force every ANCESTOR of the target open, not just the
  // target itself (§3.1). Expand state is still never persisted or written
  // to canonical state (§0 invariant 12).
  const expanded = expandedKeys.has(node.objective_key);
  const children = objectiveMapChildren(map, node);
  const gapTotal = objectiveGapTotal(map, node);

  return (
    <li>
      <div
        className={`flex flex-wrap items-center gap-2 rounded border p-2 ${isSelected ? "border-primary bg-muted" : ""}`}
        data-testid={`objective-node-${node.objective_key}`}
      >
        {(children.length > 0 || node.milestones.length > 0) && (
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={expanded ? "折りたたむ" : "展開する"}
            onClick={() => onToggle(node.objective_key)}
            className="w-5 shrink-0 text-sm text-muted-foreground"
            data-testid={`objective-node-toggle-${node.objective_key}`}
          >
            {expanded ? "▾" : "▸"}
          </button>
        )}
        <button
          type="button"
          onClick={() => onSelectObjective(node.objective_key)}
          className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:underline"
          aria-current={isSelected}
        >
          {node.title || node.objective_key}
        </button>
        <Badge variant="outline">{OBJECTIVE_STATE_LABEL[node.objective_state]}</Badge>
        <RecheckMarker state={node.recheck_state} />
        {node.milestones.length > 0 && (
          <span className="text-xs text-muted-foreground" data-testid={`objective-gap-total-${node.objective_key}`}>
            {gapTotal.unavailable ? GAP_SUMMARY_UNAVAILABLE_LABEL : `Gap ${gapTotal.count} 件`}
          </span>
        )}
      </div>

      {expanded && (
        <div className="ml-6 mt-1 space-y-1">
          {node.milestones.length > 0 && (
            <ul className="space-y-1" data-testid={`objective-milestones-${node.objective_key}`}>
              {node.milestones.map((m) => {
                const summary = m.gap_summary;
                const openLike = summary.open_count + summary.acknowledged_count + summary.deferred_count;
                const gapSummaryUnavailable = isMilestoneGapSummaryUnavailable(map, m.milestone_key);
                return (
                  <li key={m.milestone_key}>
                    <button
                      type="button"
                      onClick={() => onSelectMilestone(m.milestone_key)}
                      className={`flex w-full flex-wrap items-center gap-2 rounded border p-1.5 text-left text-xs hover:bg-muted ${
                        m.milestone_key === selectedMilestoneKey ? "border-primary bg-muted" : ""
                      }`}
                      aria-current={m.milestone_key === selectedMilestoneKey}
                      data-testid={`milestone-node-${m.milestone_key}`}
                    >
                      <span className="min-w-0 flex-1 truncate font-medium">{m.title || m.milestone_key}</span>
                      {/* 定義の確定 (design_status) と達成 (achievement) は
                          別ラベル -- 決して1つのバッジへ畳まない (§1.3)。 */}
                      <Badge variant="outline">{DESIGN_STATUS_LABEL[m.design_status]}</Badge>
                      <Badge variant={m.achievement === "met" ? "success" : "outline"}>
                        {MILESTONE_ACHIEVEMENT_LABEL[m.achievement]}
                      </Badge>
                      {m.assessability !== "assessable" && (
                        <Badge variant="outline">{MILESTONE_ASSESSABILITY_LABEL[m.assessability]}</Badge>
                      )}
                      <RecheckMarker state={m.recheck_state} />
                      {gapSummaryUnavailable ? (
                        <span className="text-muted-foreground" data-testid={`milestone-gap-summary-unavailable-${m.milestone_key}`}>
                          {GAP_SUMMARY_UNAVAILABLE_LABEL}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">未対応系 {openLike} 件 / 解消 {summary.resolved_count} 件</span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {children.length > 0 && (
            <ul className="space-y-1" data-testid={`objective-children-${node.objective_key}`}>
              {children.map((child) => (
                <ObjectiveNode
                  key={child.objective_key}
                  map={map}
                  node={child}
                  depth={depth + 1}
                  selectedObjectiveKey={selectedObjectiveKey}
                  selectedMilestoneKey={selectedMilestoneKey}
                  expandedKeys={expandedKeys}
                  onToggle={onToggle}
                  onSelectObjective={onSelectObjective}
                  onSelectMilestone={onSelectMilestone}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

export function ObjectiveTree({
  map,
  selectedObjectiveKey,
  selectedMilestoneKey,
  onSelectObjective,
  onSelectMilestone,
}: {
  map: ObjectiveMapOut;
  selectedObjectiveKey: string | null;
  selectedMilestoneKey: string | null;
  onSelectObjective: (key: string) => void;
  onSelectMilestone: (key: string) => void;
}) {
  const roots = objectiveMapRoots(map);

  // §3.1: the ancestor path of the current selection must always be
  // visible. `forcedExpandedKeys` is recomputed only when the MAP or the
  // SELECTION changes (not on every render), so the effect below fires
  // exactly on a deep link / URL change / back-forward / reload -- never on
  // an unrelated re-render.
  const forcedExpandedKeys = useMemo(
    () => objectiveMapForceExpandedKeys(map, selectedObjectiveKey, selectedMilestoneKey),
    [map, selectedObjectiveKey, selectedMilestoneKey],
  );
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set(forcedExpandedKeys));

  // Rule (§3.1, documented here because it decides what "a manually
  // collapsed ancestor" means): a NEW selection always force-opens every
  // ancestor on its path, even overriding a node the developer had manually
  // collapsed -- the point of a deep link is to reveal its target, so a
  // stale manual collapse must not be able to hide it. This only ADDS keys
  // to the expanded set; it never collapses a node the developer expanded
  // (or had force-opened for an earlier selection) that is not on the new
  // path, and the developer may collapse a force-opened ancestor again
  // immediately after -- it only reopens on the NEXT selection change.
  //
  // This adjusts state DURING RENDER rather than in an effect. React
  // documents that as the way to react to a changed input, and it is also
  // what this repo's lint rule requires: a synchronous setState inside an
  // effect renders once with the stale set and again with the corrected
  // one, so a deep link would paint with its target still hidden.
  const [lastForced, setLastForced] = useState(forcedExpandedKeys);
  if (lastForced !== forcedExpandedKeys) {
    setLastForced(forcedExpandedKeys);
    let changed = false;
    const next = new Set(expandedKeys);
    for (const key of forcedExpandedKeys) {
      if (!next.has(key)) { next.add(key); changed = true; }
    }
    if (changed) setExpandedKeys(next);
  }

  function toggle(key: string) {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  if (roots.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="objective-tree-empty">
        まだ Product Objective がありません。
      </p>
    );
  }
  return (
    <ul className="space-y-1" data-testid="objective-tree">
      {roots.map((node) => (
        <ObjectiveNode
          key={node.objective_key}
          map={map}
          node={node}
          depth={0}
          selectedObjectiveKey={selectedObjectiveKey}
          selectedMilestoneKey={selectedMilestoneKey}
          expandedKeys={expandedKeys}
          onToggle={toggle}
          onSelectObjective={onSelectObjective}
          onSelectMilestone={onSelectMilestone}
        />
      ))}
    </ul>
  );
}

/** The selected Objective's own detail line -- parent/child references,
 * state, recheck. Nothing here re-derives `objective_state`; it is read
 * straight off the node. */
export function ObjectiveDetailCard({ node }: { node: ObjectiveMapNodeOut }) {
  return (
    <div className="space-y-2 rounded border p-3" data-testid={`objective-detail-${node.objective_key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{node.title || node.objective_key}</h3>
        <Badge variant="outline">{OBJECTIVE_STATE_LABEL[node.objective_state]}</Badge>
        <RecheckMarker state={node.recheck_state} />
      </div>
      <p className="text-xs text-muted-foreground">
        {node.parent_objective_key ? `親 Objective: ${node.parent_objective_key}` : "親 Objective なし(root)"}
      </p>
      {node.milestones.length === 0 && (
        <p className="text-xs text-muted-foreground">この Objective にはまだ Milestone がありません。</p>
      )}
    </div>
  );
}
