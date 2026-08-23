// Issue #422 (Epic #418): the Stakeholder Value Network screen.
//
// Dashboard-only rendering of `GET /stakeholder-value-network`
// (`docs/stakeholder-value-network.md` §7). This screen re-derives NOTHING:
// `design_status` / `recheck_state` / `validity_state` / `evidence_state` /
// every notice arrive already decided by the server, and
// `components/stakeholder-network/model.ts` (pure, no React, no API client)
// is the only place that filters or labels them.
//
// Layout note (contract choice, see the Issue #422 report): invariant 10
// forbids computing or storing ANY coordinate/layout, so this screen never
// attempts a force-directed graph drawing -- that would require the browser
// to compute node positions, which is exactly what §0 rules out storing and
// what this screen avoids computing at all. Instead Stakeholders and Value
// Exchanges render as two linked lists (a "network as structured lists"
// view) with a detail pane -- this already IS the list + detail
// presentation §7.3 asks for below ~360px, so the screen does not need a
// second, separate narrow-width layout: the responsive grid just collapses
// the two columns into one.

import { useSearchParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { useValueNetwork } from "@/api/hooks";
import type {
  ValueNetworkDesignStatus, ValueNetworkEdgeOut, ValueNetworkExchangeKind,
  ValueNetworkNodeOut, ValueNetworkStakeholderRole,
} from "@/api/types";
import {
  AUTHORSHIP_KIND_LABEL, CADENCE_LABEL, CONSIDERATION_STATE_LABEL,
  DESIGN_STATUS_LABEL, EMPTY_FILTERS, EVIDENCE_STATE_LABEL, EXCHANGE_KIND_LABEL,
  EXCHANGE_KIND_LINE_STYLE, NOTICE_CODE_LABEL, RECHECK_STATE_LABEL, REF_KIND_LABEL,
  REF_RECHECK_STATE_LABEL, REF_RELATION_STATUS_LABEL, REF_TARGET_RESOLUTION_LABEL,
  STAKEHOLDER_KIND_LABEL, STAKEHOLDER_ROLE_LABEL, VALIDITY_STATE_LABEL,
  applyFiltersToSearchParams, displayState, edgeByKey, edgesForNode, filterEdges,
  filterNodes, filtersFromSearchParams, nodeByKey, noticesForSubject, refDeepLink,
  visibleNodeKeys, type ValueNetworkFilters,
} from "@/components/stakeholder-network/model";
import { DegradedNote, EmptyNote, LoadErrorCard, LoadingBlock, StateBadge } from "@/components/stakeholder-network/shared";
import { ValueNetworkGraph } from "@/components/stakeholder-network/graph";

const EXCHANGE_KIND_VALUES: ValueNetworkExchangeKind[] = [
  "experience", "service", "information", "money", "authority", "obligation", "risk",
];
const ROLE_VALUES: ValueNetworkStakeholderRole[] = [
  "actor", "beneficiary", "payer", "operator", "approver", "supplier", "regulator", "observer",
];
const DESIGN_STATUS_VALUES: ValueNetworkDesignStatus[] = ["proposed", "confirmed", "rejected", "retired"];

function FiltersBar({
  filters,
  onChange,
}: {
  filters: ValueNetworkFilters;
  onChange: (next: ValueNetworkFilters) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="value-network-filters">
      <Select
        aria-label="Value Exchange の種類で絞り込み"
        value={filters.exchangeKind ?? ""}
        onChange={(e) => onChange({ ...filters, exchangeKind: (e.target.value || null) as never })}
      >
        <option value="">exchange_kind: すべて</option>
        {EXCHANGE_KIND_VALUES.map((k) => (
          <option key={k} value={k}>
            {EXCHANGE_KIND_LABEL[k]}
          </option>
        ))}
      </Select>
      <Select
        aria-label="role で絞り込み"
        value={filters.role ?? ""}
        onChange={(e) => onChange({ ...filters, role: (e.target.value || null) as never })}
      >
        <option value="">role: すべて</option>
        {ROLE_VALUES.map((r) => (
          <option key={r} value={r}>
            {STAKEHOLDER_ROLE_LABEL[r]}
          </option>
        ))}
      </Select>
      <Select
        aria-label="design_status で絞り込み"
        value={filters.designStatus ?? ""}
        onChange={(e) => onChange({ ...filters, designStatus: (e.target.value || null) as never })}
      >
        <option value="">design_status: すべて</option>
        {DESIGN_STATUS_VALUES.map((s) => (
          <option key={s} value={s}>
            {DESIGN_STATUS_LABEL[s]}
          </option>
        ))}
      </Select>
      <label className="flex items-center gap-1 text-sm">
        <input
          type="checkbox"
          checked={filters.staleOnly}
          onChange={(e) => onChange({ ...filters, staleOnly: e.target.checked })}
        />
        再確認が必要なものだけ
      </label>
      {(filters.exchangeKind || filters.role || filters.designStatus || filters.staleOnly) && (
        <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_FILTERS)}>
          絞り込みを解除
        </Button>
      )}
    </div>
  );
}

/** §7.3: label + line style + legend -- never colour alone. */
function ExchangeKindLegend() {
  return (
    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground" data-testid="value-network-legend">
      {EXCHANGE_KIND_VALUES.map((k) => (
        <span key={k} className="flex items-center gap-1">
          <span aria-hidden="true">
            {k === "experience" && "───"}
            {k === "service" && "───"}
            {k === "information" && "┄┄┄"}
            {k === "money" && "══="}
            {k === "authority" && "····"}
            {k === "obligation" && "-·-·"}
            {k === "risk" && "-··-··"}
          </span>
          {EXCHANGE_KIND_LABEL[k]}({EXCHANGE_KIND_LINE_STYLE[k]})
        </span>
      ))}
    </div>
  );
}

function NodeListItem({
  node,
  selected,
  noticeCount,
  onSelect,
}: {
  node: ValueNetworkNodeOut;
  selected: boolean;
  noticeCount: number;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        data-testid={`value-network-node-${node.stakeholder_key}`}
        aria-current={selected}
        className={`w-full rounded border p-2 text-left text-sm hover:bg-muted ${selected ? "border-primary bg-muted" : ""}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{node.display_name || node.stakeholder_key}</span>
          <StateBadge label={STAKEHOLDER_KIND_LABEL[node.stakeholder_kind]} tone="outline" />
          <StateBadge label={DESIGN_STATUS_LABEL[node.design_status]} tone={node.design_status === "confirmed" ? "success" : "outline"} />
          {node.recheck_state === "stale" && <StateBadge label={RECHECK_STATE_LABEL.stale} tone="warning" />}
          {noticeCount > 0 && <StateBadge label={`要確認 ${noticeCount}件`} tone="warning" />}
        </div>
        {node.roles.length > 0 && (
          <div className="mt-1 text-xs text-muted-foreground">
            role: {node.roles.map((r) => STAKEHOLDER_ROLE_LABEL[r]).join(" / ")}
          </div>
        )}
      </button>
    </li>
  );
}

function EdgeListItem({
  edge,
  selected,
  noticeCount,
  onSelect,
}: {
  edge: ValueNetworkEdgeOut;
  selected: boolean;
  noticeCount: number;
  onSelect: () => void;
}) {
  const kindLabel = edge.exchange_kind ? EXCHANGE_KIND_LABEL[edge.exchange_kind] : "不明";
  const lineStyle = edge.exchange_kind ? EXCHANGE_KIND_LINE_STYLE[edge.exchange_kind] : "";
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        data-testid={`value-network-edge-${edge.exchange_key}`}
        aria-current={selected}
        className={`w-full rounded border p-2 text-left text-sm hover:bg-muted ${selected ? "border-primary bg-muted" : ""}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs">{edge.provider_stakeholder_key}</span>
          <span aria-hidden="true">→</span>
          <span className="font-mono text-xs">{edge.receiver_stakeholder_key}</span>
          <StateBadge label={`${kindLabel}(${lineStyle})`} tone="outline" />
          <StateBadge label={DESIGN_STATUS_LABEL[edge.design_status]} tone={edge.design_status === "confirmed" ? "success" : "outline"} />
          {edge.recheck_state === "stale" && <StateBadge label={RECHECK_STATE_LABEL.stale} tone="warning" />}
          {noticeCount > 0 && <StateBadge label={`要確認 ${noticeCount}件`} tone="warning" />}
        </div>
        <div className="mt-1 truncate text-xs text-muted-foreground">{edge.value_statement}</div>
      </button>
    </li>
  );
}

function NodeDetailPane({
  node,
  edges,
  notices,
}: {
  node: ValueNetworkNodeOut;
  edges: ValueNetworkEdgeOut[];
  notices: ReturnType<typeof noticesForSubject>;
}) {
  const { outgoing, incoming } = edgesForNode(edges, node.stakeholder_key);
  return (
    <div className="space-y-3" data-testid="value-network-node-detail">
      <div>
        <h3 className="text-lg font-semibold">{node.display_name || node.stakeholder_key}</h3>
        <p className="text-xs text-muted-foreground">stakeholder_key: {node.stakeholder_key}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <StateBadge label={STAKEHOLDER_KIND_LABEL[node.stakeholder_kind]} />
        <StateBadge label={DESIGN_STATUS_LABEL[node.design_status]} tone={node.design_status === "confirmed" ? "success" : "outline"} />
        <StateBadge label={RECHECK_STATE_LABEL[node.recheck_state]} tone={node.recheck_state === "stale" ? "warning" : "outline"} />
        <StateBadge label={AUTHORSHIP_KIND_LABEL[node.authored_by_kind]} />
        <StateBadge label={EVIDENCE_STATE_LABEL[node.evidence_state]} tone={node.evidence_state === "available" ? "success" : "outline"} />
      </div>
      <div>
        <h4 className="text-sm font-semibold">role(System scope)</h4>
        {node.roles.length === 0 ? (
          <EmptyNote>role は割り当てられていません。</EmptyNote>
        ) : (
          <p className="text-sm">{node.roles.map((r) => STAKEHOLDER_ROLE_LABEL[r]).join(" / ")}</p>
        )}
      </div>
      <div>
        <h4 className="text-sm font-semibold">提供している Value Exchange({outgoing.length})</h4>
        {outgoing.length === 0 ? (
          <EmptyNote>提供している Value Exchange はありません。</EmptyNote>
        ) : (
          <ul className="space-y-1 text-xs">
            {outgoing.map((e) => (
              <li key={e.exchange_key}>
                → {e.receiver_stakeholder_key}({e.exchange_kind ? EXCHANGE_KIND_LABEL[e.exchange_kind] : "不明"})
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h4 className="text-sm font-semibold">受け取っている Value Exchange({incoming.length})</h4>
        {incoming.length === 0 ? (
          <EmptyNote>受け取っている Value Exchange はありません。</EmptyNote>
        ) : (
          <ul className="space-y-1 text-xs">
            {incoming.map((e) => (
              <li key={e.exchange_key}>
                ← {e.provider_stakeholder_key}({e.exchange_kind ? EXCHANGE_KIND_LABEL[e.exchange_kind] : "不明"})
              </li>
            ))}
          </ul>
        )}
      </div>
      <NoticesList notices={notices} />
    </div>
  );
}

function EdgeDetailPane({
  edge,
  notices,
}: {
  edge: ValueNetworkEdgeOut;
  notices: ReturnType<typeof noticesForSubject>;
}) {
  return (
    <div className="space-y-3" data-testid="value-network-edge-detail">
      <div>
        <h3 className="text-lg font-semibold">{edge.exchange_key}</h3>
        <p className="text-xs text-muted-foreground">
          {edge.provider_stakeholder_key} → {edge.receiver_stakeholder_key}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <StateBadge label={edge.exchange_kind ? EXCHANGE_KIND_LABEL[edge.exchange_kind] : "不明"} />
        <StateBadge label={DESIGN_STATUS_LABEL[edge.design_status]} tone={edge.design_status === "confirmed" ? "success" : "outline"} />
        <StateBadge label={RECHECK_STATE_LABEL[edge.recheck_state]} tone={edge.recheck_state === "stale" ? "warning" : "outline"} />
        <StateBadge label={VALIDITY_STATE_LABEL[edge.validity_state]} />
        <StateBadge label={EVIDENCE_STATE_LABEL[edge.evidence_state]} tone={edge.evidence_state === "available" ? "success" : "outline"} />
      </div>
      <p className="text-sm">{edge.value_statement}</p>
      <div>
        <h4 className="text-sm font-semibold">対価(consideration)</h4>
        <p className="text-sm">
          {CONSIDERATION_STATE_LABEL[edge.consideration.consideration_state]}
          {edge.consideration.consideration_kind && ` — ${EXCHANGE_KIND_LABEL[edge.consideration.consideration_kind]}`}
        </p>
        {edge.consideration.consideration_statement && (
          <p className="text-xs text-muted-foreground">{edge.consideration.consideration_statement}</p>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        channel: {edge.channel || "(未指定)"} / trigger: {edge.trigger || "(未指定)"} / cadence: {CADENCE_LABEL[edge.cadence]}
      </p>
      <div>
        <h4 className="text-sm font-semibold">関連する参照(related_refs)</h4>
        {edge.related_refs.length === 0 ? (
          <EmptyNote>関連する参照はありません。</EmptyNote>
        ) : (
          <ul className="space-y-1 text-xs">
            {edge.related_refs.map((ref) => {
              const link = refDeepLink(ref.ref_kind, ref.target_ref);
              return (
                <li key={ref.id} className="rounded border p-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <StateBadge label={REF_KIND_LABEL[ref.ref_kind]} tone="outline" />
                    <span className="font-mono">{ref.target_ref}</span>
                    <StateBadge label={REF_TARGET_RESOLUTION_LABEL[ref.target_resolution]} tone={ref.target_resolution === "resolved" ? "success" : "warning"} />
                    <StateBadge label={REF_RECHECK_STATE_LABEL[ref.recheck_state]} tone={ref.recheck_state === "stale" ? "warning" : "outline"} />
                    <StateBadge label={REF_RELATION_STATUS_LABEL[ref.relation_status]} tone="outline" />
                  </div>
                  {link && (
                    <Link to={link} className="mt-1 inline-block text-xs text-primary underline">
                      関連画面を開く
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <NoticesList notices={notices} />
    </div>
  );
}

function NoticesList({ notices }: { notices: ReturnType<typeof noticesForSubject> }) {
  if (notices.length === 0) return null;
  return (
    <div data-testid="value-network-detail-notices">
      <h4 className="text-sm font-semibold">気づいたこと</h4>
      <ul className="space-y-1 text-xs">
        {notices.map((n, i) => (
          <li key={i} className="rounded border border-amber-400/50 bg-amber-50 p-1.5 text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            {NOTICE_CODE_LABEL[n.code]}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function StakeholderValueNetworkPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { data, isLoading, isError, error, refetch } = useValueNetwork();

  const filters = filtersFromSearchParams(searchParams);
  const selectedNodeKey = searchParams.get("node");
  const selectedEdgeKey = searchParams.get("edge");

  function updateParams(mutate: (params: URLSearchParams) => void) {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  }

  function onFiltersChange(next: ValueNetworkFilters) {
    updateParams((params) => applyFiltersToSearchParams(params, next));
  }

  function selectNode(key: string) {
    updateParams((params) => {
      params.set("node", key);
      params.delete("edge");
    });
  }

  function selectEdge(key: string) {
    updateParams((params) => {
      params.set("edge", key);
      params.delete("node");
    });
  }

  const nodes = data?.nodes ?? [];
  const edges = data?.edges ?? [];
  const notices = data?.notices ?? [];

  const filteredNodes = filterNodes(nodes, filters);
  const filteredEdges = filterEdges(edges, filters);
  const nodeKeys = visibleNodeKeys(filteredNodes, filteredEdges, filters);
  const displayedNodes = filteredNodes.filter((n) => nodeKeys.has(n.stakeholder_key));

  const selectedNode = nodeByKey(nodes, selectedNodeKey);
  const selectedEdge = edgeByKey(edges, selectedEdgeKey);

  const state = displayState(data, displayedNodes);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Stakeholder Value Network</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Stakeholder(当事者)と Value Exchange(何を渡し何を受け取るか)の関係を確認します。
          状態・staleness・気づいたことはすべてサーバーの判定をそのまま表示し、この画面では
          再計算しません。表示位置(レイアウト)は保存されず、確定・却下などの判断もこの画面
          からは行いません。
        </p>
      </div>

      {isLoading && <LoadingBlock />}
      {isError && (
        <LoadErrorCard
          detail={(error as { message?: string } | null)?.message}
          onRetry={() => void refetch()}
        />
      )}

      {data && (
        <>
          <DegradedNote sections={data.degraded_sections} detail={data.degraded_detail} />
          <ExchangeKindLegend />
          <FiltersBar filters={filters} onChange={onFiltersChange} />

          {state === "no_data" && (
            <EmptyNote testId="value-network-no-data">まだありません。Stakeholder / Value Exchange を登録してください。</EmptyNote>
          )}
          {state === "filtered_empty" && (
            <EmptyNote testId="value-network-filtered-empty">
              絞り込み条件に一致する Stakeholder / Value Exchange がありません。
            </EmptyNote>
          )}

          {state === "ready" && (
            <Card className="hidden md:block">
              <CardHeader>
                <CardTitle as="h2" className="text-base">
                  関係図
                </CardTitle>
              </CardHeader>
              <CardContent>
                {/* §7.3: Stakeholder が節点、Value Exchange が方向付き edge。
                    座標は描画のたびに算出するだけで保存しない(不変条件 10)。
                    狭幅では下の一覧+詳細へ縮退する ——
                    同じ内容が一覧側にもあるので情報は失われない。 */}
                <ValueNetworkGraph
                  nodes={displayedNodes}
                  edges={filteredEdges}
                  selectedNodeKey={selectedNodeKey}
                  selectedEdgeKey={selectedEdgeKey}
                  onSelectNode={selectNode}
                  onSelectEdge={selectEdge}
                />
              </CardContent>
            </Card>
          )}

          {state === "ready" && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_1fr_1.2fr]">
              <Card>
                <CardHeader>
                  <CardTitle as="h2" className="text-base">
                    Stakeholder({displayedNodes.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-2" data-testid="value-network-node-list">
                    {displayedNodes.map((n) => (
                      <NodeListItem
                        key={n.stakeholder_key}
                        node={n}
                        selected={selectedNodeKey === n.stakeholder_key}
                        noticeCount={noticesForSubject(notices, "stakeholder", n.stakeholder_key).length}
                        onSelect={() => selectNode(n.stakeholder_key)}
                      />
                    ))}
                  </ul>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle as="h2" className="text-base">
                    Value Exchange({filteredEdges.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {filteredEdges.length === 0 ? (
                    <EmptyNote>該当する Value Exchange はありません。</EmptyNote>
                  ) : (
                    <ul className="space-y-2" data-testid="value-network-edge-list">
                      {filteredEdges.map((e) => (
                        <EdgeListItem
                          key={e.exchange_key}
                          edge={e}
                          selected={selectedEdgeKey === e.exchange_key}
                          noticeCount={noticesForSubject(notices, "value_exchange", e.exchange_key).length}
                          onSelect={() => selectEdge(e.exchange_key)}
                        />
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle as="h2" className="text-base">
                    詳細
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {selectedNode ? (
                    <NodeDetailPane
                      node={selectedNode}
                      edges={edges}
                      notices={noticesForSubject(notices, "stakeholder", selectedNode.stakeholder_key)}
                    />
                  ) : selectedEdge ? (
                    <EdgeDetailPane
                      edge={selectedEdge}
                      notices={noticesForSubject(notices, "value_exchange", selectedEdge.exchange_key)}
                    />
                  ) : (
                    <EmptyNote testId="value-network-no-selection">
                      左の一覧から Stakeholder または Value Exchange を選択してください。
                    </EmptyNote>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </>
      )}
    </div>
  );
}
