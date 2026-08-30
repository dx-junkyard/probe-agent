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

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useGapWorkbench, useObjectiveMap } from "@/api/hooks";
import {
  EMPTY_GAP_WORKBENCH_FILTERS, filterGapWorkbenchEntries, objectiveMapEmptyState,
  objectiveMapNodeByKey, objectiveMapSelectionFromSearchParams,
  normalizeObjectiveMapSelection,
  applyObjectiveMapSelectionToSearchParams, type GapWorkbenchFilters,
} from "@/components/product-objective/model";
import {
  UnsavedWorkProvider, confirmDiscardUnsavedWork, useUnsavedWork,
} from "@/components/product-objective/unsaved-work";
import { useSlowPending } from "@/components/product-objective/use-slow-pending";
import { ObjectiveDetailCard, ObjectiveTree } from "@/components/product-objective/objective-tree";
import {
  CreateGapForm, GapDetailPanel, GapEntryList, GapWorkbenchFiltersBar, SourceKindBreakdown,
} from "@/components/product-objective/gap-workbench-panel";
import {
  CreateMilestoneForm, CreateObjectiveForm, MilestoneWorkPanel, ObjectiveWorkPanel,
} from "@/components/product-objective/objective-forms";

/** §3.3/§9.5: a lane never shows an indefinite bare skeleton. Before the
 * `useSlowPending` threshold this is just a named skeleton; after it, the
 * section names itself, says why it is still waiting, and offers 再試行. */
function LanePending({ testId, label, slow, onRetry }: {
  testId: string;
  label: string;
  slow: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="space-y-2" data-testid={testId}>
      <p className="text-sm text-muted-foreground">{label} を読み込んでいます…</p>
      <Skeleton className="h-24 w-full" />
      {slow && (
        <div
          className="rounded border border-amber-400/50 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200"
          data-testid={`${testId}-slow`}
        >
          <p>{label} の読み込みに時間がかかっています。しばらく待っても表示されない場合は再試行してください。</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>再試行</Button>
        </div>
      )}
    </div>
  );
}

function LaneError({ testId, label, onRetry }: { testId: string; label: string; onRetry: () => void }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm" role="alert" data-testid={testId}>
      <p className="font-medium text-destructive">{label} を取得できませんでした。</p>
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>再試行</Button>
    </div>
  );
}

/** One lane's content: shows its OWN loading/error/retry independently of
 * the other lane (§3.3), so an already-loaded lane is usable immediately
 * while the other is still pending. */
function Lane({ query, pendingTestId, errorTestId, label, children }: {
  query: { isLoading: boolean; data: unknown; refetch: () => void };
  pendingTestId: string;
  errorTestId: string;
  label: string;
  children: ReactNode;
}) {
  const slow = useSlowPending(query.isLoading);
  if (query.isLoading) {
    return <LanePending testId={pendingTestId} label={label} slow={slow} onRetry={() => query.refetch()} />;
  }
  if (!query.data) {
    return <LaneError testId={errorTestId} label={label} onRetry={() => query.refetch()} />;
  }
  return <>{children}</>;
}

/** The provider must sit ABOVE the component that asks the question, so the
 * page body is one level in. */
export default function ObjectiveMapPage() {
  return (
    <UnsavedWorkProvider>
      <ObjectiveMapPageBody />
    </UnsavedWorkProvider>
  );
}

function ObjectiveMapPageBody() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { systemId } = useAuth();
  const objectiveMap = useObjectiveMap();
  const gapWorkbench = useGapWorkbench();

  // A Milestone is never shown without the Objective that owns it: a
  // Milestone-only deep link resolves its owner from the SERVER's own map,
  // and selecting a Milestone under a manually expanded Objective moves the
  // Objective selection with it. See `normalizeObjectiveMapSelection` -- the
  // tree already reveals such a Milestone, but the detail/action pane is
  // keyed off `objectiveKey`, so without this it never mounts.
  const selection = normalizeObjectiveMapSelection(
    objectiveMap.data, objectiveMapSelectionFromSearchParams(searchParams),
  );
  const [gapFilters, setGapFilters] = useState<GapWorkbenchFilters>({
    ...EMPTY_GAP_WORKBENCH_FILTERS,
    objectiveKey: selection.objectiveKey,
    milestoneKey: selection.milestoneKey,
  });

  // Review 0.4's other half. Keying the work panels per entity stopped text
  // typed for A from being SUBMITTED as B's, but a remount discards it
  // silently -- so clicking the next row in a list still loses work without
  // warning. Ask before an ENTITY changes; a lane switch is not an entity
  // change and never prompts.
  const unsavedWork = useUnsavedWork();
  function changesEntity(patch: Partial<typeof selection>): boolean {
    return (
      (patch.objectiveKey !== undefined && patch.objectiveKey !== selection.objectiveKey)
      || (patch.milestoneKey !== undefined && patch.milestoneKey !== selection.milestoneKey)
      || (patch.gapKey !== undefined && patch.gapKey !== selection.gapKey)
    );
  }

  function updateSelection(patch: Partial<typeof selection>) {
    if (changesEntity(patch) && unsavedWork?.hasUnsavedWork() && !confirmDiscardUnsavedWork()) {
      return;
    }
    const next = normalizeObjectiveMapSelection(objectiveMap.data, { ...selection, ...patch });
    const params = new URLSearchParams(searchParams);
    applyObjectiveMapSelectionToSearchParams(params, next);
    setSearchParams(params, { replace: true });
  }

  // The normalized selection is what the page acts on, so the URL must say
  // the same thing -- otherwise a reload or a shared link reopens the
  // un-normalized (Objective-less, or wrong-Objective) view. This one IS an
  // effect rather than a during-render adjustment: it writes to the ROUTER,
  // an external system, not to this component's own state, and the render
  // already uses the normalized value, so nothing is painted wrong while it
  // catches up. `replace` keeps it out of the history stack -- it corrects
  // the entry the developer arrived on, it is not a navigation of their own.
  const canonicalSearch = (() => {
    const params = new URLSearchParams(searchParams);
    applyObjectiveMapSelectionToSearchParams(params, selection);
    return params.toString();
  })();
  useEffect(() => {
    if (canonicalSearch !== searchParams.toString()) {
      setSearchParams(new URLSearchParams(canonicalSearch), { replace: true });
    }
  }, [canonicalSearch, searchParams, setSearchParams]);

  // §3.4 rule (documented here because it decides what "single source of
  // truth" means for the two representations of the same value): the URL
  // decides `selection.objectiveKey` / `selection.milestoneKey`, and the Gap
  // Workbench filter dropdowns for the SAME two fields re-sync to match
  // every time that URL-derived selection changes -- a tree click, a deep
  // link, back/forward, or a reload. This can override an objective/milestone
  // the developer had picked directly in the filter dropdowns, but only when
  // the selection itself just changed; `lifecycle` has no URL representation
  // and is untouched by this effect (cleared only by the explicit
  // 絞り込みを解除 control).
  //
  // Adjusted during render rather than in an effect, for the same reason as
  // `objective-tree.tsx`: a synchronous setState in an effect paints once
  // with the previous selection's filters before correcting them.
  const [lastSelectionKeys, setLastSelectionKeys] = useState({
    objectiveKey: selection.objectiveKey,
    milestoneKey: selection.milestoneKey,
  });
  if (
    lastSelectionKeys.objectiveKey !== selection.objectiveKey ||
    lastSelectionKeys.milestoneKey !== selection.milestoneKey
  ) {
    setLastSelectionKeys({
      objectiveKey: selection.objectiveKey,
      milestoneKey: selection.milestoneKey,
    });
    setGapFilters((prev) => ({
      ...prev,
      objectiveKey: selection.objectiveKey,
      milestoneKey: selection.milestoneKey,
    }));
  }

  // §3.4: an Objective/Milestone/Gap key from the PREVIOUS System has no
  // meaning in the newly selected one -- keeping it around either resolves
  // to nothing (a silently empty detail pane) or, worse, happens to match an
  // unrelated row. Clear the URL selection and the Gap Workbench filters on
  // every System switch (never on the initial mount, which is when a
  // deep link into this System's own data must still be honoured).
  const previousSystemIdRef = useRef(systemId);
  useEffect(() => {
    if (previousSystemIdRef.current === systemId) return;
    previousSystemIdRef.current = systemId;
    setGapFilters(EMPTY_GAP_WORKBENCH_FILTERS);
    const params = new URLSearchParams(searchParams);
    applyObjectiveMapSelectionToSearchParams(params, { ...selection, objectiveKey: null, milestoneKey: null, gapKey: null });
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemId]);

  // §3.3: only a total inability to show anything (both lanes settled AND
  // both failed) blocks the whole page. Anything else -- one or both still
  // loading, or only one failed -- renders the Tabs immediately and lets
  // each lane report its own state (`Lane` above).
  const bothSettled = !objectiveMap.isLoading && !gapWorkbench.isLoading;
  const bothFailed = bothSettled
    && (objectiveMap.isError || !objectiveMap.data) && (gapWorkbench.isError || !gapWorkbench.data);

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
  const emptyState = map && workbench ? objectiveMapEmptyState(map, workbench) : null;
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
          <Lane
            query={objectiveMap}
            pendingTestId="objective-map-lane-loading"
            errorTestId="objective-map-lane-error"
            label="Objective Map"
          >
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
                      {/* `key` is contract, not a hint: the revision and
                          decision forms inside hold local `useState`, so
                          without a remount per entity, text typed for
                          Objective A stays on screen after selecting B and
                          「記録する」 saves it as B's revision -- a wrong-entity
                          write, not a cosmetic leftover. The System id is part
                          of the key because these slugs are unique only WITHIN
                          a System (§1's `(system_id, <kind>_key)` identity). */}
                      <ObjectiveWorkPanel
                        key={`${systemId ?? "none"}:${selectedNode.objective_key}`}
                        objectiveKey={selectedNode.objective_key}
                      />
                      <CreateMilestoneForm
                        objectiveKey={selectedNode.objective_key}
                        onCreated={(key) => updateSelection({ milestoneKey: key })}
                      />
                      {selection.milestoneKey && (
                        <>
                          <MilestoneWorkPanel
                            key={`${systemId ?? "none"}:${selection.milestoneKey}`}
                            milestoneKey={selection.milestoneKey}
                          />
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
          </Lane>
        </TabsContent>

        <TabsContent value="gaps" data-testid="objective-map-panel-gaps">
          <Lane
            query={gapWorkbench}
            pendingTestId="gap-workbench-lane-loading"
            errorTestId="gap-workbench-lane-error"
            label="Gap Workbench"
          >
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
                      <GapDetailPanel
                        key={`${systemId ?? "none"}:${selection.gapKey}`}
                        gapKey={selection.gapKey}
                        workbench={workbench}
                      />
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
          </Lane>
        </TabsContent>
      </Tabs>
    </div>
  );
}
