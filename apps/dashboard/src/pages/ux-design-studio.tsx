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
import { JourneyPanel } from "@/components/ux-design/journey-panel";
import { RequirementPanel } from "@/components/ux-design/requirement-panel";
import { SolutionDesignPanel } from "@/components/ux-design/solution-design-panel";

type StudioTab = "journeys" | "requirements" | "solutions";

function isStudioTab(v: string | null): v is StudioTab {
  return v === "journeys" || v === "requirements" || v === "solutions";
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
    goToTab("requirements");
  }

  function openSolutionDesign(key: string) {
    setDesignKey(key);
    goToTab("solutions");
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
            onSelect={setJourneyKey}
            onOpenRequirement={openRequirement}
          />
        </TabsContent>

        <TabsContent value="requirements" data-testid="ux-design-studio-panel-requirements">
          <RequirementPanel
            selectedKey={requirementKey}
            onSelect={setRequirementKey}
            onOpenSolutionDesign={openSolutionDesign}
          />
        </TabsContent>

        <TabsContent value="solutions" data-testid="ux-design-studio-panel-solutions">
          <SolutionDesignPanel selectedKey={designKey} onSelect={setDesignKey} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
