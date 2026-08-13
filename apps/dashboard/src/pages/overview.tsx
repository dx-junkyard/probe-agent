import { useOverview } from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FindingsCard } from "@/components/overview/findings";
import { LoopRailCard, NextActionCard } from "@/components/overview/next-action";
import { RuntimeHealthCard } from "@/components/overview/runtime-health";
import { SystemBriefCard } from "@/components/overview/system-brief";
import { targetHref } from "@/components/overview/display";

// Issue #380: the Overview is the System Intelligence Brief / decision
// cockpit, not a metrics screen.
//
// Order in the main column is contract, not styling — the same discipline
// #358 fixed on the Interview cockpit. Reading top to bottom must answer the
// Epic's five questions in order:
//
//   1. System Brief      -> 何のためのシステムで、AI はどう理解しているか
//   2. 今わかったこと      -> 前回から何が変わり、何が分かったか
//   3. 次にすること        -> 次の1操作と、その理由・完了条件・完了後の価値
//   4. 改善ループの現在地   -> どこまで来ていて、次の意味的到達点は何か
//   5. Runtime health     -> いま観測できているか（二次領域）
//
// The heading order and the DOM order are the same, so a screen reader gets
// the same priority ordering as the eye.
//
// Everything semantic arrives already decided by `GET /overview`. This page
// chooses layout and disclosure; it never picks a CTA, re-ranks a finding, or
// re-derives a readiness verdict from counts (#380 UX原則 6).

export default function OverviewPage() {
  const { systems, systemId } = useAuth();
  const { data: overview, isLoading, isError, refetch } = useOverview();
  const system = systems.find((s) => s.id === systemId);

  // Zero Systems is not a state the System-scoped endpoint can be asked about,
  // so it is answered here. It uses the same finite action vocabulary
  // (`create_system`) rather than growing a second one.
  if (systems.length === 0) {
    return (
      <div className="space-y-4">
        <PageHeading system={undefined} />
        <Card data-testid="overview-no-systems">
          <CardHeader>
            <CardTitle className="text-base">次にすること</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm" data-action-key="create_system">
            <p className="font-medium">System を作成してください。</p>
            <p className="text-muted-foreground">
              System は観測対象のまとまりです。ヘッダーの「System を作成」から作成すると、
              リポジトリの登録とシステム理解の構築に進めます。
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading) {
    // A skeleton in the same shape as the loaded page, so nothing shifts when
    // it resolves — and so 「読み込み中」 never looks like 「空」 (#384).
    return (
      <div className="space-y-4" data-testid="overview-loading">
        <PageHeading system={system} />
        <div className="grid gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-56 w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (isError || !overview) {
    return (
      <div className="space-y-4">
        <PageHeading system={system} />
        <Card data-testid="overview-load-error">
          <CardHeader>
            <CardTitle className="text-base">Overview を取得できませんでした</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-muted-foreground">
              状態が分からないため、推測での案内は行いません。
            </p>
            <button
              type="button"
              className="rounded-md border px-3 py-1.5 text-sm hover:bg-accent"
              onClick={() => refetch()}
            >
              再試行
            </button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const interviewHref = targetHref({
    route: "/interview",
    label: "",
    params: overview.interview_session_id
      ? { session: String(overview.interview_session_id) }
      : {},
    anchor: null,
  });

  return (
    <div className="space-y-4">
      <PageHeading system={system} />

      {overview.degraded_sections.length > 0 && (
        // A partial failure names what is missing and lets the rest render.
        // The whole screen never goes blank because one section could not be
        // derived (#384).
        <Card data-testid="overview-degraded">
          <CardContent className="p-4 text-sm">
            <p className="font-medium">一部の情報を取得できませんでした。</p>
            <p className="text-muted-foreground">
              取得できなかった領域: {overview.degraded_sections.join(", ")}。
              表示中の他の領域は最新の永続事実に基づいています。
            </p>
          </CardContent>
        </Card>
      )}

      {/* Desktop: the Brief holds the main area and everything else sits in the
          ADJACENT column — the arrangement #384 explicitly allows, and the one
          the first-view measurement forced. Stacking them under the Brief put
          「今わかったこと」 at 824px on a 1280×720 screen: the reading order was
          right, but two of the four things the developer must see in the first
          view were below the fold. Source order is unchanged by the split, so
          the single-column stack below `xl` still reads
          Brief → findings → next action → loop → runtime.
          `items-start` keeps the short column from stretching to the tall one. */}
      <div className="grid items-start gap-4 xl:grid-cols-12">
        <div className="xl:col-span-5">
          <SystemBriefCard overview={overview} interviewHref={interviewHref} />
        </div>
        <div className="xl:col-span-4">
          <FindingsCard overview={overview} />
        </div>
        <div className="space-y-4 xl:col-span-3">
          <NextActionCard overview={overview} />
          <LoopRailCard overview={overview} />
          <RuntimeHealthCard overview={overview} />
        </div>
      </div>
    </div>
  );
}

function PageHeading({ system }: { system?: { name: string; environment?: string | null } }) {
  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
      {system ? (
        <p className="mt-1 text-muted-foreground">
          {system.name}
          {system.environment ? ` — ${system.environment}` : ""}
        </p>
      ) : (
        <p className="mt-1 text-muted-foreground">
          このシステムについて分かっていること、変わったこと、次にすることをまとめます。
        </p>
      )}
    </div>
  );
}
