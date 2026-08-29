// Issue #432 (Epic #427): the Objective Map, with the Gap Workbench as its
// second lane (`docs/product-objective-lineage.md` §9.4 -- "Gap Workbench は
// 独立ページを作らない").
//
// This screen re-derives NOTHING: `objective_state` / `design_status` /
// `achievement` / `assessability` / `lifecycle` / `priority_band` /
// `recheck_state` / `source_state` / ordering / `deep_link_state` all arrive
// already decided by `GET /objective-map` and `GET /gap-workbench`.
// `components/product-objective/model.ts` (pure, no React, no API client) is
// the only place that filters, labels, or resolves ids into nodes.
//
// Two lanes, one URL: `/objective-map` (Objective tree) and
// `/objective-map?view=gaps` (Gap Workbench), deep-linkable to one Gap with
// `&gap=<gap_key>`. Selecting anything only changes the URL/detail pane; the
// manual actions in the Gap detail pane are the only writes this screen ever
// performs, and only on explicit developer submission (§9.2).

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useGapWorkbench, useObjectiveMap } from "@/api/hooks";
import {
  EMPTY_GAP_WORKBENCH_FILTERS, filterGapWorkbenchEntries, objectiveMapEmptyState,
  objectiveMapNodeByKey, objectiveMapSelectionFromSearchParams,
  applyObjectiveMapSelectionToSearchParams, type GapWorkbenchFilters,
} from "@/components/product-objective/model";
import { ObjectiveDetailCard, ObjectiveTree } from "@/components/product-objective/objective-tree";
import {
  CreateGapForm, GapDetailPanel, GapEntryList, GapWorkbenchFiltersBar, SourceKindBreakdown,
} from "@/components/product-objective/gap-workbench-panel";
import {
  CreateMilestoneForm, CreateObjectiveForm, MilestoneWorkPanel, ObjectiveWorkPanel,
} from "@/components/product-objective/objective-forms";

export default function ObjectiveMapPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const objectiveMap = useObjectiveMap();
  const gapWorkbench = useGapWorkbench();

  const selection = objectiveMapSelectionFromSearchParams(searchParams);
  const [gapFilters, setGapFilters] = useState<GapWorkbenchFilters>({
    ...EMPTY_GAP_WORKBENCH_FILTERS,
    objectiveKey: selection.objectiveKey,
    milestoneKey: selection.milestoneKey,
  });

  function updateSelection(patch: Partial<typeof selection>) {
    const next = { ...selection, ...patch };
    const params = new URLSearchParams(searchParams);
    applyObjectiveMapSelectionToSearchParams(params, next);
    setSearchParams(params, { replace: true });
  }

  const loading = objectiveMap.isLoading || gapWorkbench.isLoading;
  const bothFailed = (objectiveMap.isError || !objectiveMap.data) && (gapWorkbench.isError || !gapWorkbench.data);

  if (loading) {
    return (
      <div className="space-y-4 p-4 md:p-6" data-testid="objective-map-loading">
        <h1 className="text-xl font-semibold">Objective Map</h1>
        <p className="text-sm text-muted-foreground">Objective / Milestone / Gap を読み込んでいます…</p>
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (bothFailed) {
    return (
      <div className="space-y-4 p-4 md:p-6">
        <h1 className="text-xl font-semibold">Objective Map</h1>
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm" role="alert" data-testid="objective-map-load-error">
          <p className="font-medium text-destructive">取得できませんでした</p>
          <div className="mt-2 flex gap-2">
            <Button variant="outline" size="sm" onClick={() => objectiveMap.refetch()}>Objective Map を再試行</Button>
            <Button variant="outline" size="sm" onClick={() => gapWorkbench.refetch()}>Gap Workbench を再試行</Button>
          </div>
        </div>
      </div>
    );
  }

  const map = objectiveMap.data;
  const workbench = gapWorkbench.data;
  const emptyState = map ? objectiveMapEmptyState(map, workbench ?? null) : "no_objective";
  const selectedNode = map ? objectiveMapNodeByKey(map, selection.objectiveKey) : null;
  const filteredEntries = workbench
    ? filterGapWorkbenchEntries(workbench.entries, { ...gapFilters, milestoneKey: gapFilters.milestoneKey ?? selection.milestoneKey })
    : [];

  return (
    <div className="space-y-4 p-4 md:p-6" data-testid="objective-map-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Objective Map</h1>
          <p className="text-sm text-muted-foreground">
            Vision へ寄与する Product Objective / Milestone / Gap を追跡します。
          </p>
        </div>
      </div>

      {(map?.degraded_sections.length || workbench?.degraded_sections.length) ? (
        <div
          className="rounded border border-amber-400/50 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200"
          data-testid="objective-map-degraded"
        >
          一部の情報を取得できませんでした:{" "}
          {[...(map?.degraded_sections ?? []), ...(workbench?.degraded_sections ?? [])].join(", ")}
        </div>
      ) : null}

      {emptyState === "no_objective" && (
        <p className="text-sm text-muted-foreground" data-testid="objective-map-empty-no-objective">
          この System にはまだ Product Objective がありません。Vision へ近づくための中間目標を作成してください。
        </p>
      )}
      {emptyState === "no_gap" && (
        <p className="text-sm text-muted-foreground" data-testid="objective-map-empty-no-gap">
          Objective は作成されていますが、まだ Gap は 1 件も整理されていません。
        </p>
      )}

      <Tabs value={selection.view} onValueChange={(v) => updateSelection({ view: v as "objectives" | "gaps" })}>
        <TabsList data-testid="objective-map-tabs">
          <TabsTrigger value="objectives" data-testid="objective-map-tab-objectives">Objective Map</TabsTrigger>
          <TabsTrigger value="gaps" data-testid="objective-map-tab-gaps">Gap Workbench</TabsTrigger>
        </TabsList>

        <TabsContent value="objectives" data-testid="objective-map-panel-objectives">
          {!map && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm" role="alert" data-testid="objective-map-lane-error">
              <p className="font-medium text-destructive">Objective Map を取得できませんでした。</p>
              <Button variant="outline" size="sm" className="mt-2" onClick={() => objectiveMap.refetch()}>再試行</Button>
            </div>
          )}
          {map && (
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle as="h2" className="text-base">Objective 階層</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <CreateObjectiveForm onCreated={(key) => updateSelection({ objectiveKey: key, milestoneKey: null })} />
                  <ObjectiveTree
                    map={map}
                    selectedObjectiveKey={selection.objectiveKey}
                    selectedMilestoneKey={selection.milestoneKey}
                    onSelectObjective={(key) => updateSelection({ objectiveKey: key, milestoneKey: null })}
                    onSelectMilestone={(key) => updateSelection({ milestoneKey: key })}
                  />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle as="h2" className="text-base">詳細</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {selectedNode ? (
                    <>
                      <ObjectiveDetailCard node={selectedNode} />
                      <ObjectiveWorkPanel objectiveKey={selectedNode.objective_key} />
                      <CreateMilestoneForm
                        objectiveKey={selectedNode.objective_key}
                        onCreated={(key) => updateSelection({ milestoneKey: key })}
                      />
                      {selection.milestoneKey && (
                        <>
                          <MilestoneWorkPanel milestoneKey={selection.milestoneKey} />
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => updateSelection({ view: "gaps", milestoneKey: selection.milestoneKey })}
                            data-testid="objective-map-go-to-milestone-gaps"
                          >
                            この Milestone の Gap を Gap Workbench で見る
                          </Button>
                        </>
                      )}
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground" data-testid="objective-detail-empty">
                      Objective を選択すると詳細が表示されます。
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </TabsContent>

        <TabsContent value="gaps" data-testid="objective-map-panel-gaps">
          {!workbench && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm" role="alert" data-testid="gap-workbench-lane-error">
              <p className="font-medium text-destructive">Gap Workbench を取得できませんでした。</p>
              <Button variant="outline" size="sm" className="mt-2" onClick={() => gapWorkbench.refetch()}>再試行</Button>
            </div>
          )}
          {workbench && (
            <div className="space-y-3">
              <SourceKindBreakdown workbench={workbench} />
              <CreateGapForm
                map={map}
                defaultMilestoneKey={gapFilters.milestoneKey ?? selection.milestoneKey}
                onCreated={(gapKey) => updateSelection({ view: "gaps", gapKey })}
              />
              <GapWorkbenchFiltersBar filters={gapFilters} onChange={setGapFilters} map={map} />
              <div className="grid gap-4 md:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle as="h2" className="text-base">
                      Gap ({filteredEntries.length}
                      {filteredEntries.length !== workbench.entries.length ? ` / 全 ${workbench.entries.length}` : ""})
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <GapEntryList
                      entries={filteredEntries}
                      selectedGapKey={selection.gapKey}
                      onSelect={(gapKey) => updateSelection({ gapKey })}
                    />
                  </CardContent>
                </Card>
                <Card data-testid="gap-workbench-detail-pane">
                  <CardHeader>
                    <CardTitle as="h2" className="text-base">詳細</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {selection.gapKey ? (
                      <GapDetailPanel gapKey={selection.gapKey} workbench={workbench} />
                    ) : (
                      <p className="text-sm text-muted-foreground" data-testid="gap-detail-empty">
                        Gap を選択すると詳細と操作が表示されます。
                      </p>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
