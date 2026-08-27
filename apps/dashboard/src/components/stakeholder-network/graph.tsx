// Issue #422 (Epic #418): §7.3's directed graph — Stakeholders as nodes,
// Value Exchanges as directed edges.
//
// This component DRAWS a graph; it is not a record of one. Invariant 10
// forbids persisting coordinates or auto-layout results and forbids treating
// a rendered graph as canonical — it does not forbid rendering. Every
// coordinate here comes from `computeGraphLayout`, is recomputed on each
// render from the server's own total ordering, and is never written to the
// server, to `stakeholder_view_preference`, or to any request body.
//
// Nothing semantic is decided here. `design_status` / `recheck_state` /
// `validity_state` / `evidence_state` and every notice arrive DECIDED by
// `GET /stakeholder-value-network` (§0 invariant 9); this file only positions
// and labels them.
//
// Exchange kind is conveyed by THREE independent channels — a text label on
// every edge, a distinct `stroke-dasharray`, and the legend — so it is never
// carried by colour alone. The same rule applies to state: a stale or
// unconfirmed edge carries a text marker, not just a different stroke.

import { useMemo } from "react";

import {
  EXCHANGE_KIND_DASH,
  EXCHANGE_KIND_LABEL,
  GRAPH_NODE_RADIUS,
  STAKEHOLDER_KIND_LABEL,
  computeGraphLayout,
  graphEdgePath,
} from "./model";
import type { ValueNetworkEdgeOut, ValueNetworkNodeOut } from "../../api/types";

interface ValueNetworkGraphProps {
  nodes: readonly ValueNetworkNodeOut[];
  edges: readonly ValueNetworkEdgeOut[];
  selectedNodeKey: string | null;
  selectedEdgeKey: string | null;
  onSelectNode: (stakeholderKey: string) => void;
  onSelectEdge: (exchangeKey: string) => void;
}

/** A short marker shown next to an edge/node label when its state needs the
 * reader's attention. Text, never colour alone. */
function attentionMarker(designStatus: string, recheckState: string): string {
  if (recheckState === "stale") return "要再確認";
  if (designStatus === "proposed") return "未確認";
  if (designStatus === "rejected") return "却下";
  if (designStatus === "retired") return "廃止";
  return "";
}

export function ValueNetworkGraph({
  nodes,
  edges,
  selectedNodeKey,
  selectedEdgeKey,
  onSelectNode,
  onSelectEdge,
}: ValueNetworkGraphProps) {
  const layout = useMemo(() => computeGraphLayout(nodes, edges), [nodes, edges]);
  const edgeByKey = useMemo(
    () => new Map(edges.map((edge) => [edge.exchange_key, edge])),
    [edges],
  );
  const nodeByKey = useMemo(
    () => new Map(nodes.map((node) => [node.stakeholder_key, node])),
    [nodes],
  );

  return (
    <div className="overflow-x-auto" data-testid="value-network-graph">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        style={{ maxWidth: layout.width, height: "auto" }}
        role="img"
        aria-label="Stakeholder Value Network の関係図。Stakeholder が節点、Value Exchange が提供者から受領者への矢印です。同じ内容は下の一覧でも確認できます。"
      >
        <defs>
          <marker
            id="value-network-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
        </defs>

        {layout.edges.map((laidOut) => {
          const edge = edgeByKey.get(laidOut.exchange_key);
          if (edge === undefined) return null;
          const isSelected = selectedEdgeKey === edge.exchange_key;
          const marker = attentionMarker(edge.design_status, edge.recheck_state);
          // A null `exchange_kind` means the Exchange has no current
          // revision. That is a real state and reads as 不明 -- it is never
          // silently rendered as one of the seven kinds.
          const kindLabel = edge.exchange_kind ? EXCHANGE_KIND_LABEL[edge.exchange_kind] : "不明";
          const midX = (laidOut.x1 + laidOut.x2) / 2;
          const midY = (laidOut.y1 + laidOut.y2) / 2;
          return (
            <g
              key={edge.exchange_key}
              data-testid={`value-network-graph-edge-${edge.exchange_key}`}
              className="cursor-pointer"
              role="button"
              tabIndex={0}
              aria-label={`Value Exchange ${edge.exchange_key}: ${kindLabel}${marker === "" ? "" : `（${marker}）`}`}
              onClick={() => onSelectEdge(edge.exchange_key)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectEdge(edge.exchange_key);
                }
              }}
            >
              <path
                d={graphEdgePath(laidOut)}
                fill="none"
                strokeWidth={isSelected ? 3 : 1.5}
                strokeDasharray={edge.exchange_kind ? EXCHANGE_KIND_DASH[edge.exchange_kind] || undefined : "3 3"}
                markerEnd="url(#value-network-arrow)"
                className={isSelected ? "stroke-foreground" : "stroke-muted-foreground"}
              />
              <text
                x={midX}
                y={midY - 6}
                textAnchor="middle"
                className="fill-foreground text-[10px]"
              >
                {kindLabel}
                {marker === "" ? "" : `・${marker}`}
              </text>
            </g>
          );
        })}

        {layout.nodes.map((laidOut) => {
          const node = nodeByKey.get(laidOut.stakeholder_key);
          if (node === undefined) return null;
          const isSelected = selectedNodeKey === node.stakeholder_key;
          const marker = attentionMarker(node.design_status, node.recheck_state);
          return (
            <g
              key={node.stakeholder_key}
              data-testid={`value-network-graph-node-${node.stakeholder_key}`}
              className="cursor-pointer"
              role="button"
              tabIndex={0}
              aria-label={`Stakeholder ${node.display_name}（${STAKEHOLDER_KIND_LABEL[node.stakeholder_kind]}）${marker === "" ? "" : `（${marker}）`}`}
              onClick={() => onSelectNode(node.stakeholder_key)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectNode(node.stakeholder_key);
                }
              }}
            >
              <circle
                cx={laidOut.x}
                cy={laidOut.y}
                r={GRAPH_NODE_RADIUS}
                strokeWidth={isSelected ? 3 : 1.5}
                className={
                  isSelected
                    ? "fill-accent stroke-foreground"
                    : "fill-background stroke-muted-foreground"
                }
              />
              <text
                x={laidOut.x}
                y={laidOut.y - 4}
                textAnchor="middle"
                className="fill-foreground text-[11px] font-medium"
              >
                {node.display_name.slice(0, 8)}
              </text>
              <text
                x={laidOut.x}
                y={laidOut.y + 10}
                textAnchor="middle"
                className="fill-muted-foreground text-[9px]"
              >
                {STAKEHOLDER_KIND_LABEL[node.stakeholder_kind]}
              </text>
              {marker === "" ? null : (
                <text
                  x={laidOut.x}
                  y={laidOut.y + 24}
                  textAnchor="middle"
                  className="fill-foreground text-[9px]"
                >
                  {marker}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
