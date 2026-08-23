// Issue #423 (Epic #418): the Journey Service Blueprint screen.
//
// `docs/stakeholder-value-network.md` §8/§9.4. Dashboard-only rendering of
// `GET /journey-blueprint` / `GET /journey-blueprint/diff` -- the server
// decides every lane state, staleness, and diff entry; this page and
// `components/journey-blueprint/*` never re-derive any of them (§0
// invariant 9). Selection (Journey + as-is/to-be switch) lives in the URL
// so a reload or a shared link reproduces the view (§7.3's rule, applied
// here one layer over from the Value Network screen).

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useJourneyBlueprint, useJourneyBlueprintDiff, useUxJourneys } from "@/api/hooks";
import { BlueprintGrid } from "@/components/journey-blueprint/blueprint-grid";
import { BlueprintDetailPane } from "@/components/journey-blueprint/detail-pane";
import { BlueprintDiffPanel } from "@/components/journey-blueprint/diff-panel";
import { BLUEPRINT_BASELINE_STATE_LABEL } from "@/components/journey-blueprint/model";
import type { BlueprintLaneCellOut } from "@/api/types";

type ViewMode = "blueprint" | "diff";

function isViewMode(v: string | null): v is ViewMode {
  return v === "blueprint" || v === "diff";
}

export default function JourneyBlueprintPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const journeys = useUxJourneys();

  const [journeyKey, setJourneyKey] = useState<string | null>(searchParams.get("journey"));
  const [view, setView] = useState<ViewMode>(isViewMode(searchParams.get("view")) ? (searchParams.get("view") as ViewMode) : "blueprint");
  const [selected, setSelected] = useState<{ stepKey: string; cell: BlueprintLaneCellOut } | null>(null);

  // Auto-select the first Journey once, on first load -- never override an
  // explicit "no Journey selected" the developer navigated away from
  // (#349's "re-selecting on every render makes an unselected state
  // unreachable" rule, applied here).
  useEffect(() => {
    if (journeyKey === null && journeys.data && journeys.data.journeys.length > 0 && searchParams.get("journey") === null) {
      setJourneyKey(journeys.data.journeys[0].journey_key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [journeys.data]);

  function updateParams(next: { journey?: string | null; view?: ViewMode }) {
    const params = new URLSearchParams(searchParams);
    if (next.journey !== undefined) {
      if (next.journey === null) params.delete("journey");
      else params.set("journey", next.journey);
    }
    if (next.view !== undefined) params.set("view", next.view);
    setSearchParams(params, { replace: true });
  }

  function onSelectJourney(key: string) {
    setJourneyKey(key || null);
    setSelected(null);
    updateParams({ journey: key || null });
  }

  function onSelectView(next: ViewMode) {
    setView(next);
    updateParams({ view: next });
  }

  // §9.4 / #358: the CTA NAVIGATES to the screen that owns the Requirement,
  // it never executes anything here.
  function openRequirement(requirementKey: string) {
    navigate(`/ux-design-studio?tab=requirements&requirement=${encodeURIComponent(requirementKey)}`);
  }

  const blueprint = useJourneyBlueprint(journeyKey);
  const diff = useJourneyBlueprintDiff(view === "diff" ? journeyKey : null);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Journey Service Blueprint</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Journey の各 Step を横軸に、9 つのレーン(利用者の行動・接点・フロントステージ・
          バックステージ・サポート業務・外部連携・要件・エビデンス・失敗と復旧)を縦軸に
          表示します。状態はすべてサーバーの判定をそのまま表示し、この画面では再計算しません。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            Journey を選択
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Select
            data-testid="blueprint-journey-select"
            value={journeyKey ?? ""}
            onChange={(e) => onSelectJourney(e.target.value)}
            className="w-64"
          >
            <option value="">(未選択)</option>
            {(journeys.data?.journeys ?? []).map((j) => (
              <option key={j.journey_key} value={j.journey_key}>
                {j.journey_key} ({j.perspective === "as_is" ? "現状" : "目標"})
              </option>
            ))}
          </Select>

          <div className="flex gap-2">
            <Button
              size="sm"
              variant={view === "blueprint" ? "default" : "outline"}
              data-testid="blueprint-view-blueprint"
              onClick={() => onSelectView("blueprint")}
            >
              Blueprint
            </Button>
            <Button
              size="sm"
              variant={view === "diff" ? "default" : "outline"}
              data-testid="blueprint-view-diff"
              onClick={() => onSelectView("diff")}
            >
              as-is / to-be 差分
            </Button>
          </div>
        </CardContent>
      </Card>

      {journeyKey === null ? (
        <p className="text-sm text-muted-foreground" data-testid="blueprint-no-journey">
          Journey を選択してください。
        </p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
          <div className="space-y-4">
            {view === "blueprint" ? (
              blueprint.isLoading ? (
                <p className="text-sm text-muted-foreground" data-testid="blueprint-loading">
                  読み込み中です。
                </p>
              ) : blueprint.isError ? (
                <p className="text-sm text-destructive" data-testid="blueprint-error">
                  取得できませんでした。
                </p>
              ) : blueprint.data ? (
                <>
                  <p className="text-xs text-muted-foreground" data-testid="blueprint-baseline-state">
                    {BLUEPRINT_BASELINE_STATE_LABEL[blueprint.data.baseline_state]}
                  </p>
                  {blueprint.data.degraded_sections.length > 0 ? (
                    <p className="text-xs text-amber-600" data-testid="blueprint-degraded">
                      一部のセクションを取得できませんでした:{" "}
                      {blueprint.data.degraded_sections.join(", ")}
                    </p>
                  ) : null}
                  <BlueprintGrid
                    blueprint={blueprint.data}
                    onSelectCell={(stepKey, cell) => setSelected({ stepKey, cell })}
                  />
                </>
              ) : null
            ) : (
              <BlueprintDiffPanel diff={diff.data} />
            )}
          </div>

          {view === "blueprint" ? (
            <BlueprintDetailPane
              stepKey={selected?.stepKey ?? null}
              cell={selected?.cell ?? null}
              onOpenRequirement={openRequirement}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}
