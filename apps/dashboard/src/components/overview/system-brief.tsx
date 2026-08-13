import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatTimestamp } from "@/lib/utils";
import { CONFIRMATION_VARIANT, READINESS_VARIANT } from "./display";
import type { OverviewOut, UnderstandingBriefClaimOut } from "@/api/types";

// Issue #381: the first view answers 「このシステムは何のためにあり、AI はどう
// 理解し、その理解で進めるか」 before it answers anything about counts.
//
// The Brief and the Readiness verdict are the SAME canonical projection the
// Interview screen renders (#351-#354). This component re-derives neither: it
// reads `confirmation` / `provenance` / `readiness_state` and their
// server-supplied Japanese labels. There is no third understanding model here.

/**
 * One claim, with 確認状態 and 出所 shown as two separate badges.
 *
 * They are two independent axes and must stay visibly separate: approving an
 * AI-written sentence does not make the developer its author, and a confirmed
 * claim the system later changed must not keep reading 「確認済み」.
 */
function Claim({ claim, testId }: { claim: UnderstandingBriefClaimOut; testId: string }) {
  return (
    <li className="rounded-md border p-3" data-testid={testId} data-confirmation={claim.confirmation}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="font-medium">{claim.name}</span>
        <span className="flex flex-wrap gap-1">
          <Badge variant={CONFIRMATION_VARIANT[claim.confirmation]}>
            {claim.confirmation_label}
          </Badge>
          <Badge variant="outline">{claim.provenance_label}</Badge>
          {claim.is_mock && <Badge variant="warning">mock</Badge>}
        </span>
      </div>
      {claim.summary && (
        <p className="mt-1 text-sm text-muted-foreground">{claim.summary}</p>
      )}
      {claim.contribution && (
        <p className="mt-1 text-xs text-muted-foreground">目的への貢献: {claim.contribution}</p>
      )}
    </li>
  );
}

export function SystemBriefCard({
  overview,
  interviewHref,
}: {
  overview: OverviewOut;
  interviewHref: string;
}) {
  const brief = overview.brief;

  if (overview.degraded_sections.includes("brief") || !brief) {
    // Degraded, not empty. Saying 「理解がありません」 here would assert a fact
    // about the system from a failure to read one (#384).
    return (
      <Card data-testid="overview-brief-unavailable">
        <CardHeader>
          <CardTitle className="text-base">System Brief</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>現在のシステム理解を取得できませんでした。</p>
          <p className="text-muted-foreground">
            表示できないのは要約だけです。理解そのものは
            <Link to={interviewHref} className="text-primary underline">システム理解</Link>
            の画面で確認できます。
          </p>
        </CardContent>
      </Card>
    );
  }

  const capabilities = brief.core_capabilities.slice(
    0,
    brief.core_capability_initial_count,
  );
  const hiddenCapabilities = brief.core_capabilities.length - capabilities.length;

  return (
    <Card data-testid="overview-system-brief">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <CardTitle className="text-base">System Brief</CardTitle>
        <Badge
          variant={READINESS_VARIANT[brief.readiness_state]}
          data-testid="overview-readiness-badge"
          data-readiness={brief.readiness_state}
        >
          {brief.readiness_label}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Readiness is judgement material, not a permission. It explains; the
            single primary action stays visible either way (#353). */}
        <p className="text-sm text-muted-foreground" data-testid="overview-readiness-description">
          {brief.readiness_description}
        </p>
        {brief.readiness_reasons.length > 0 && (
          <ul className="space-y-1 text-xs" data-testid="overview-readiness-reasons">
            {brief.readiness_reasons.slice(0, 4).map((reason) => (
              <li key={`${reason.code}-${reason.target_name}`} className="flex gap-2">
                <Badge
                  variant={
                    reason.severity === "blocking"
                      ? "destructive"
                      : reason.severity === "attention"
                        ? "warning"
                        : "secondary"
                  }
                >
                  {reason.severity === "blocking"
                    ? "進行不可"
                    : reason.severity === "attention"
                      ? "要確認"
                      : "参考"}
                </Badge>
                <span className="text-muted-foreground">{reason.message}</span>
              </li>
            ))}
          </ul>
        )}

        <section data-testid="overview-vision">
          <h3 className="text-sm font-semibold">Vision — 誰の状態をどう変えたいか</h3>
          {brief.vision ? (
            <ul className="mt-2">
              <Claim claim={brief.vision} testId="overview-vision-claim" />
            </ul>
          ) : (
            // Never a synthesised sentence. 「推定できない」 is itself a fact,
            // and the missing information is named so it can be supplied.
            <div className="mt-2 rounded-md border border-dashed p-3 text-sm" data-testid="overview-vision-missing">
              <p>Vision はまだ分かっていません。コードだけでは決まらない内容です。</p>
              {brief.vision_missing_information.length > 0 && (
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  {brief.vision_missing_information.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
              <Link to={interviewHref} className="mt-2 inline-block text-xs text-primary underline">
                Vision を定義する
              </Link>
            </div>
          )}
        </section>

        <section data-testid="overview-system-purpose">
          <h3 className="text-sm font-semibold">
            System Purpose — その Vision に対してこのシステムが担う役割
          </h3>
          {brief.system_purpose.length > 0 ? (
            <ul className="mt-2 space-y-2">
              {brief.system_purpose.map((claim) => (
                <Claim key={claim.name} claim={claim} testId="overview-purpose-claim" />
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground" data-testid="overview-purpose-missing">
              System Purpose をまだ抽出できていません。
            </p>
          )}
        </section>

        <section data-testid="overview-core-capabilities">
          <h3 className="text-sm font-semibold">主要機能 (Core Capabilities)</h3>
          {capabilities.length > 0 ? (
            <ul className="mt-2 space-y-2">
              {capabilities.map((claim) => (
                <Claim key={claim.name} claim={claim} testId="overview-capability-claim" />
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground" data-testid="overview-capabilities-missing">
              主要機能をまだ抽出できていません。
            </p>
          )}
          {hiddenCapabilities > 0 && (
            <Link
              to={interviewHref}
              className="mt-2 inline-block text-xs text-primary underline"
              data-testid="overview-capabilities-more"
            >
              残り {hiddenCapabilities} 件と根拠を見る
            </Link>
          )}
        </section>

        {/* Progressive disclosure: the full tree, the evidence, the API
            boundaries and the supporting elements all live on the Interview
            screen. The Overview links to them, it does not reimplement them. */}
        <details data-testid="overview-brief-details">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            対象 Snapshot・リビジョン・詳細への入口
          </summary>
          <dl className="mt-2 space-y-1 text-xs text-muted-foreground">
            <div>
              <dt className="inline font-medium">対象 Snapshot: </dt>
              <dd className="inline">
                {brief.snapshot_id ?? "—"}
                {overview.snapshot_commit_sha && (
                  <code className="ml-1 font-mono">
                    {overview.snapshot_commit_sha.slice(0, 8)}
                  </code>
                )}
                {overview.latest_ready_snapshot_id != null &&
                  brief.snapshot_id != null &&
                  overview.latest_ready_snapshot_id !== brief.snapshot_id && (
                    <span className="ml-1 text-amber-700 dark:text-amber-300">
                      （最新の snapshot は {overview.latest_ready_snapshot_id}）
                    </span>
                  )}
              </dd>
            </div>
            <div>
              <dt className="inline font-medium">理解のリビジョン: </dt>
              <dd className="inline">{brief.revision_id ?? "—"}</dd>
            </div>
            <div>
              <dt className="inline font-medium">最後に確認した時点: </dt>
              <dd className="inline">
                {brief.confirmed_at != null ? formatTimestamp(brief.confirmed_at) : "未確認"}
              </dd>
            </div>
            {Object.entries(brief.detail_counts).map(([section, count]) => (
              <div key={section}>
                <dt className="inline font-medium">{section}: </dt>
                <dd className="inline">{count} 件</dd>
              </div>
            ))}
          </dl>
          <Link to={interviewHref} className="mt-2 inline-block text-xs text-primary underline">
            全文の理解と根拠を開く
          </Link>
        </details>
      </CardContent>
    </Card>
  );
}
