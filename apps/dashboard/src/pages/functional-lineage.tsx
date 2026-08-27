// Issue #424 (Epic #418): the Functional Lineage View + Gap/Impact Overlay.
//
// Dashboard-only rendering of `GET /functional-lineage`
// (`docs/stakeholder-value-network.md` §9). This screen re-derives NOTHING:
// `kind` / gap `code` / `severity` all arrive already decided by the
// server, and `components/functional-lineage/model.ts` (pure, no React, no
// API client) is the only place that filters, labels, or walks the
// downstream-impact graph.
//
// Cross-view navigation (§9.4): this screen, `/stakeholder-value-network`,
// and `/journey-blueprint` share one selected ref through the `ref_kind` /
// `ref` URL params (`readSharedSelection`/`writeSharedSelection`) and one
// System scope (the existing `X-Probe-System-Id` header, untouched by this
// param pair) -- navigating between them never loses either. Selecting a
// gap shows its evidence and the single next operation that would resolve
// it as a deep link into the EXISTING screen that owns that operation
// (`lineageDeepLink`); the CTA navigates, it never executes.

import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useFunctionalLineage } from "@/api/hooks";
import type { FunctionalLineageGapOut, FunctionalLineageKind, LineageGapSeverity } from "@/api/types";
import {
  EMPTY_LINEAGE_FILTERS, GAP_CODE_LABEL, GAP_SEVERITY_LABEL, GAP_SEVERITY_MARKER,
  LINEAGE_KIND_LABEL, applyLineageFiltersToSearchParams, displayState, filterGaps, filterNodes,
  gapCountBySeverity, gapsForSubject, lineageDeepLink, lineageFiltersFromSearchParams, nodeByRef,
  readSharedSelection, traceDownstreamImpact, writeSharedSelection, type LineageFilters,
} from "@/components/functional-lineage/model";

const KIND_VALUES: FunctionalLineageKind[] = [
  "stakeholder", "stakeholder_need", "purpose_element", "purpose_relation",
  "capability", "value_exchange", "ux_journey", "ux_journey_step",
  "ux_requirement", "solution_design", "static_flow", "runtime_flow",
  "evolution_node", "component", "cell_definition", "cell_binding",
  "probe_point", "purpose_outcome_criterion",
];
const SEVERITY_VALUES: LineageGapSeverity[] = ["blocking", "attention", "informational"];

function FiltersBar({ filters, onChange }: { filters: LineageFilters; onChange: (next: LineageFilters) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="lineage-filters">
      <Select
        aria-label="種類で絞り込み"
        value={filters.kind ?? ""}
        onChange={(e) => onChange({ ...filters, kind: (e.target.value || null) as never })}
      >
        <option value="">種類: すべて</option>
        {KIND_VALUES.map((k) => (
          <option key={k} value={k}>{LINEAGE_KIND_LABEL[k]}</option>
        ))}
      </Select>
      <Select
        aria-label="Gap の深刻度で絞り込み"
        value={filters.gapSeverity ?? ""}
        onChange={(e) => onChange({ ...filters, gapSeverity: (e.target.value || null) as never })}
      >
        <option value="">Gap: すべて</option>
        {SEVERITY_VALUES.map((s) => (
          <option key={s} value={s}>{GAP_SEVERITY_LABEL[s]}</option>
        ))}
      </Select>
      {(filters.kind || filters.gapSeverity) && (
        <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_LINEAGE_FILTERS)}>
          絞り込みを解除
        </Button>
      )}
    </div>
  );
}

/** §9.4's legend: label + shape/marker, never colour alone (#358). */
function SeverityLegend() {
  return (
    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground" data-testid="lineage-legend">
      {SEVERITY_VALUES.map((s) => (
        <span key={s}>
          {GAP_SEVERITY_MARKER[s]} {GAP_SEVERITY_LABEL[s]}
        </span>
      ))}
    </div>
  );
}

function GapRow({ gap, onSelect }: { gap: FunctionalLineageGapOut; onSelect: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        data-testid={`lineage-gap-${gap.code}-${gap.subject_kind}-${gap.subject_ref}`}
        className="w-full rounded border p-2 text-left text-sm hover:bg-muted"
      >
        <div className="flex items-center justify-between gap-2">
          <span>
            {GAP_SEVERITY_MARKER[gap.severity]} {GAP_CODE_LABEL[gap.code]}
          </span>
          <Badge variant="outline">{GAP_SEVERITY_LABEL[gap.severity]}</Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {LINEAGE_KIND_LABEL[gap.subject_kind]}: {gap.subject_ref}
        </p>
      </button>
    </li>
  );
}

export default function FunctionalLineagePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = useFunctionalLineage();

  const [filters, setFilters] = useState<LineageFilters>(() => lineageFiltersFromSearchParams(searchParams));
  const selection = readSharedSelection(searchParams);

  function updateFilters(next: LineageFilters) {
    setFilters(next);
    const params = new URLSearchParams(searchParams);
    applyLineageFiltersToSearchParams(params, next);
    setSearchParams(params, { replace: true });
  }

  function select(kind: FunctionalLineageKind, ref: string) {
    const params = new URLSearchParams(searchParams);
    writeSharedSelection(params, { kind, ref });
    setSearchParams(params, { replace: true });
  }

  function clearSelection() {
    const params = new URLSearchParams(searchParams);
    writeSharedSelection(params, { kind: null, ref: null });
    setSearchParams(params, { replace: true });
  }

  if (query.isLoading) {
    return (
      <div className="space-y-4 p-4 md:p-6" data-testid="lineage-loading">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="space-y-4 p-4 md:p-6">
        <h1 className="text-xl font-semibold">Functional Lineage</h1>
        <div
          className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm"
          data-testid="lineage-load-error"
          role="alert"
        >
          <p className="font-medium text-destructive">取得できませんでした</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => query.refetch()}>
            再試行
          </Button>
        </div>
      </div>
    );
  }

  const data = query.data;
  const filteredNodes = filterNodes(data.nodes, filters);
  const filteredGaps = filterGaps(data.gaps, filters);
  const state = displayState(data, filteredNodes);
  const counts = gapCountBySeverity(data.gaps);

  const selectedNode = nodeByRef(data.nodes, selection.kind, selection.ref);
  const selectedGaps = selection.kind && selection.ref ? gapsForSubject(data.gaps, selection.kind, selection.ref) : [];
  const downstreamImpact = selection.kind && selection.ref
    ? traceDownstreamImpact(data.edges, selection.kind, selection.ref)
    : [];
  const selectedDeepLink = selection.kind && selection.ref ? lineageDeepLink(selection.kind, selection.ref) : null;

  return (
    <div className="space-y-4 p-4 md:p-6" data-testid="functional-lineage-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Functional Lineage</h1>
        <div className="flex gap-2 text-sm">
          <Badge variant="outline">対応が必要 {counts.blocking}</Badge>
          <Badge variant="outline">要確認 {counts.attention}</Badge>
          <Badge variant="outline">参考情報 {counts.informational}</Badge>
        </div>
      </div>

      {data.degraded_sections.length > 0 && (
        <div
          className="rounded border border-amber-400/50 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200"
          data-testid="lineage-degraded"
        >
          一部の情報を取得できませんでした: {data.degraded_sections.join(", ")}
        </div>
      )}

      <FiltersBar filters={filters} onChange={updateFilters} />
      <SeverityLegend />

      {state === "no_data" && (
        <p className="text-sm text-muted-foreground" data-testid="lineage-empty">
          まだ何も記録されていません。
        </p>
      )}
      {state === "filtered_empty" && (
        <p className="text-sm text-muted-foreground" data-testid="lineage-filtered-empty">
          この絞り込みに一致するものがありません。
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>チェーン上のエンティティ ({filteredNodes.length})</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1" data-testid="lineage-node-list">
              {filteredNodes.map((n) => (
                <li key={`${n.kind}:${n.ref}`}>
                  <button
                    type="button"
                    data-testid={`lineage-node-${n.kind}-${n.ref}`}
                    aria-current={selection.kind === n.kind && selection.ref === n.ref}
                    onClick={() => select(n.kind, n.ref)}
                    className={`w-full rounded border p-2 text-left text-sm hover:bg-muted ${
                      selection.kind === n.kind && selection.ref === n.ref ? "border-primary bg-muted" : ""
                    }`}
                  >
                    <span className="text-xs text-muted-foreground">{LINEAGE_KIND_LABEL[n.kind]}</span>
                    <br />
                    <span className="font-medium">{n.name || n.ref}</span>
                  </button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Gap ({filteredGaps.length})</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1" data-testid="lineage-gap-list">
              {filteredGaps.map((g) => (
                <GapRow key={`${g.code}:${g.subject_kind}:${g.subject_ref}`} gap={g} onSelect={() => select(g.subject_kind, g.subject_ref)} />
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      {selectedNode && (
        <Card data-testid="lineage-detail-pane">
          <CardHeader>
            <CardTitle>
              {LINEAGE_KIND_LABEL[selectedNode.kind]}: {selectedNode.name || selectedNode.ref}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button variant="ghost" size="sm" onClick={clearSelection}>選択を解除</Button>

            <div>
              <h3 className="text-sm font-semibold">この対象の Gap</h3>
              {selectedGaps.length === 0 ? (
                <p className="text-sm text-muted-foreground">Gap はありません。</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {selectedGaps.map((g) => (
                    <li key={g.code} className="text-sm">
                      {GAP_SEVERITY_MARKER[g.severity]} {GAP_CODE_LABEL[g.code]}
                      <Badge variant="outline" className="ml-2">{GAP_SEVERITY_LABEL[g.severity]}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {selectedDeepLink && (
              <div>
                <h3 className="text-sm font-semibold">次の操作</h3>
                <Link to={selectedDeepLink} className="text-sm text-primary underline" data-testid="lineage-resolve-link">
                  この対象を扱う画面を開く
                </Link>
              </div>
            )}

            <div>
              <h3 className="text-sm font-semibold">下流への影響 ({downstreamImpact.length})</h3>
              {downstreamImpact.length === 0 ? (
                <p className="text-sm text-muted-foreground">下流に影響する対象はありません。</p>
              ) : (
                <ul className="mt-1 space-y-1" data-testid="lineage-impact-list">
                  {downstreamImpact.map((entry) => (
                    <li key={`${entry.kind}:${entry.ref}`} className="text-sm">
                      <button
                        type="button"
                        onClick={() => select(entry.kind, entry.ref)}
                        className="text-left hover:underline"
                      >
                        {LINEAGE_KIND_LABEL[entry.kind]}: {entry.ref}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
