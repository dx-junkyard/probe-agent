// Issue #409 (Epic #405): the UX Design Studio.
//
// Dashboard-only -- no new endpoint, no server judgement re-derived here
// (`docs/ux-design-lineage.md` §0 invariant 9). Four levels of progressive
// disclosure (§4.2): Journey list -> a Journey's Step sequence ->
// the Requirements linked to a Step -> the adopted Solution Design and its
// implementation targets. The three tabs below are the three top-level
// entity kinds this layer persists (§1); moving between levels within a
// Journey/Requirement/Solution Design happens inside each tab's own detail
// panel, and crossing tabs (Step -> Requirement -> Solution Design) is a
// plain navigate driven by the callbacks passed into each panel -- never an
// execute (#358).
//
// Naming note (§4, and `.claude/skills/dashboard/SKILL.md`): this is the UX
// Design Studio, not the Evolution Node "Design Studio" (#397 Phase 2). The
// two are unrelated screens; do not merge their copy or their routes.

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSolutionDesigns, useUxJourneys, useUxRequirements } from "@/api/hooks";
import { decideNextDesignAction } from "@/components/ux-design/model";
import { JourneyPanel } from "@/components/ux-design/journey-panel";
import { RequirementPanel } from "@/components/ux-design/requirement-panel";
import { SolutionDesignPanel } from "@/components/ux-design/solution-design-panel";

type StudioTab = "journeys" | "requirements" | "solutions";

function isStudioTab(v: string | null): v is StudioTab {
  return v === "journeys" || v === "requirements" || v === "solutions";
}

/** §4.2's single 「今決めるべきこと」. The decision itself is
 * `decideNextDesignAction`'s first-match table over server-decided fields;
 * this component only renders it, and its CTA navigates -- the operation
 * still belongs to the destination panel (§4.1 / #358). The two no-action
 * outcomes render as a sentence, never as a disabled button (#342 原則 P3). */
function NextDecisionCard({
  onGo,
}: {
  onGo: (tab: StudioTab, key: string | null) => void;
}) {
  const journeys = useUxJourneys();
  const requirements = useUxRequirements();
  const designs = useSolutionDesigns();

  const loading = journeys.isLoading || requirements.isLoading || designs.isLoading;
  const decision = decideNextDesignAction({
    journeys: journeys.data?.journeys ?? null,
    requirements: requirements.data?.requirements ?? null,
    designs: designs.data?.designs ?? null,
  });

  return (
    <Card data-testid="ux-design-next-decision">
      <CardHeader>
        <CardTitle as="h2" className="text-base">
          次に決めること
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading ? (
          <p className="text-sm text-muted-foreground" data-testid="ux-design-next-decision-loading">
            読み込み中です。
          </p>
        ) : (
          <>
            <p className="text-sm font-medium" data-testid="ux-design-next-decision-title">
              {decision.title}
            </p>
            <p className="text-xs text-muted-foreground" data-testid="ux-design-next-decision-reason">
              {decision.reason}
            </p>
            {decision.target && decision.actionLabel ? (
              <Button
                size="sm"
                data-testid="ux-design-next-decision-cta"
                onClick={() => onGo(decision.target!.tab, decision.target!.key)}
              >
                {decision.actionLabel}
              </Button>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function UxDesignStudioPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = searchParams.get("tab");
  const [tab, setTab] = useState<StudioTab>(isStudioTab(initialTab) ? initialTab : "journeys");
  const [journeyKey, setJourneyKey] = useState<string | null>(searchParams.get("journey"));
  const [requirementKey, setRequirementKey] = useState<string | null>(searchParams.get("requirement"));
  const [designKey, setDesignKey] = useState<string | null>(searchParams.get("design"));

  function goToTab(next: StudioTab) {
    setTab(next);
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  }

  function openRequirement(key: string) {
    setRequirementKey(key);
    const params = new URLSearchParams(searchParams);
    params.set("requirement", key);
    params.set("tab", "requirements");
    setSearchParams(params, { replace: true });
    setTab("requirements");
  }

  function openSolutionDesign(key: string) {
    setDesignKey(key);
    const params = new URLSearchParams(searchParams);
    params.set("design", key);
    params.set("tab", "solutions");
    setSearchParams(params, { replace: true });
    setTab("solutions");
  }

  function selectJourney(key: string | null) {
    setJourneyKey(key);
    const params = new URLSearchParams(searchParams);
    if (key) params.set("journey", key);
    else params.delete("journey");
    setSearchParams(params, { replace: true });
  }

  function selectRequirement(key: string | null) {
    setRequirementKey(key);
    const params = new URLSearchParams(searchParams);
    if (key) params.set("requirement", key);
    else params.delete("requirement");
    setSearchParams(params, { replace: true });
  }

  function selectSolutionDesign(key: string | null) {
    setDesignKey(key);
    const params = new URLSearchParams(searchParams);
    if (key) params.set("design", key);
    else params.delete("design");
    setSearchParams(params, { replace: true });
  }

  function goToNextDecision(nextTab: StudioTab, key: string | null) {
    const params = new URLSearchParams(searchParams);
    if (key !== null) {
      if (nextTab === "journeys") {
        setJourneyKey(key);
        params.set("journey", key);
      }
      if (nextTab === "requirements") {
        setRequirementKey(key);
        params.set("requirement", key);
      }
      if (nextTab === "solutions") {
        setDesignKey(key);
        params.set("design", key);
      }
    }
    setTab(nextTab);
    params.set("tab", nextTab);
    setSearchParams(params, { replace: true });
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">UX Design Studio</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          UX Journey / Requirement / Solution Design の設計成果物を確認・記述します。
          状態・差分・staleness はすべてサーバーの判定をそのまま表示し、この画面では
          再計算しません。設計案の採用は実装への適用・policy 変更・publish を
          一切行いません。
        </p>
      </div>

      <NextDecisionCard onGo={goToNextDecision} />

      <Tabs value={tab} onValueChange={(v) => goToTab(v as StudioTab)}>
        <TabsList data-testid="ux-design-studio-tabs">
          <TabsTrigger value="journeys" data-testid="ux-design-studio-tab-journeys">
            UX Journey
          </TabsTrigger>
          <TabsTrigger value="requirements" data-testid="ux-design-studio-tab-requirements">
            Requirement
          </TabsTrigger>
          <TabsTrigger value="solutions" data-testid="ux-design-studio-tab-solutions">
            Solution Design
          </TabsTrigger>
        </TabsList>

        <TabsContent value="journeys" data-testid="ux-design-studio-panel-journeys">
          <JourneyPanel
            selectedKey={journeyKey}
            onSelect={selectJourney}
            onOpenRequirement={openRequirement}
          />
        </TabsContent>

        <TabsContent value="requirements" data-testid="ux-design-studio-panel-requirements">
          <RequirementPanel
            selectedKey={requirementKey}
            onSelect={selectRequirement}
            onOpenSolutionDesign={openSolutionDesign}
          />
        </TabsContent>

        <TabsContent value="solutions" data-testid="ux-design-studio-panel-solutions">
          <SolutionDesignPanel selectedKey={designKey} onSelect={selectSolutionDesign} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
