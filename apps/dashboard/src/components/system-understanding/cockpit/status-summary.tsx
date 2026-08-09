// Issue #356 §2 / Issue #360: Interview status サマリー。
//
// #356 では完成度 (大きな百分率 + 進捗バー) が主役で、「次にやること」は右端の
// 補助カラムだった。#358 の実測では、この画面で最も強く描かれているものが
// 「今すぐ実行できる操作」ではないため、開発者は現在地を読んだあと作業面まで
// 約 1,100px スクロールしていた。
//
// #360 で主役を入れ替える: 「次にやること」と 1 つの主 CTA を先頭に置き、
// 完成度・件数はその下の一行の統計へ降ろす (受け入れ条件「完成度・件数は主
// CTA より視覚的に強くならない」)。
//
// この CTA は **実行しない**。移動とフォーカスだけを行い、状態の完了条件を
// 満たす操作は作業面のボタンが唯一の実行者であり続ける (#342 原則 P1: 1 状態
// 1 主操作)。判定・文言はすべて `buildCockpitModel` の `nextStep` が決めた
// 値で、ここでは描くだけである。

import { ArrowRight, Lightbulb } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { CockpitModel } from "./model";

export function CockpitStatusSummary({
  model,
  actionLabel,
  onRunNextStep,
}: {
  model: CockpitModel;
  /**
   * 主 CTA の文言。`null` なら CTA を描かない -- 移動先も実行対象も無い
   * ときに空のボタンを置かない (前提未達の主操作を見せない、原則 P3)。
   */
  actionLabel: string | null;
  onRunNextStep: () => void;
}) {
  const percent = model.completionPercent;
  // 取得できていない値は 0 ではなく「—」。0 件と未取得を同じ表示にしない。
  const questionTotal = model.qa ? String(model.qa.total) : "—";
  // 要確認は Q&A 未取得のとき「今わかっている分」の下限にすぎないので、
  // 確定値として出さない (0 件だと問題なしに読めてしまう)。未設定は内容の
  // 有無だけで決まるため常に確定値。
  const reviewValue = model.countsSettled
    ? String(model.reviewCount)
    : `${model.reviewCount}件以上`;
  const stats: Array<[string, string, string]> = [
    // 完成度は独立した大きな数字ではなく、他の件数と同じ密度の 1 タイル。
    ["cockpit-completion-percent", percent == null ? "—" : `${percent}%`, "完成度"],
    ["cockpit-stat-categories", String(model.categoryCount), "理解カテゴリ"],
    ["cockpit-stat-review", reviewValue, "要確認"],
    ["cockpit-stat-missing", String(model.missingCount), "未設定"],
    ["cockpit-stat-questions", questionTotal, "質問合計"],
  ];
  const unavailableNote =
    model.qaFetchStatus === "unavailable"
      ? "Q&A を取得できていないため、質問と完成度を確定できません。"
      : model.qaFetchStatus === "loading"
        ? "Q&A を読み込み中のため、質問と完成度はまだ確定していません。"
        : null;

  return (
    <Card data-testid="cockpit-status-summary">
      <CardContent className="space-y-4 p-4">
        {/* 主役。ファーストビューで最も強い要素はここ 1 つだけ。 */}
        <div data-testid="cockpit-next-step">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Lightbulb className="h-4 w-4" /> 次にやること
          </div>
          <h2
            className="mt-1 text-lg font-semibold tracking-tight break-words"
            data-testid="cockpit-next-step-title"
          >
            {model.nextStep.title}
          </h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground break-words">
            {model.nextStep.description}
          </p>
          {actionLabel && (
            <Button
              className="mt-3"
              onClick={onRunNextStep}
              data-testid="cockpit-next-step-action"
            >
              {actionLabel}
              <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </div>

        {/* 補助。完成度は数字と細い進捗バーだけにし、CTA より強くしない。 */}
        <div className="border-t pt-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Interview status
          </p>
          <p className="mt-1 text-xs text-muted-foreground" data-testid="cockpit-completion-headline">
            {percent == null
              ? "理解の完成度はまだ確定できません"
              : `理解の全体像は ${percent}% まで固まっています`}
          </p>
          {percent == null ? (
            <p
              className="mt-2 rounded-md border border-dashed p-2 text-xs text-muted-foreground"
              data-testid="cockpit-completion-unavailable"
            >
              {unavailableNote ?? "判定できないカテゴリがあるため、完成度を算出できません。"}
            </p>
          ) : (
            <div
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              aria-label={`インタビューの完成度 ${percent}%`}
              data-testid="cockpit-progress-bar"
            >
              <div className="h-full rounded-full bg-emerald-500" style={{ width: `${percent}%` }} />
            </div>
          )}
          <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {stats.map(([testId, value, label]) => (
              <div key={testId} className="rounded-md border px-2 py-1" data-testid={testId}>
                <dd className="text-sm font-semibold leading-none break-words">{value}</dd>
                <dt className="mt-1 text-[11px] text-muted-foreground">{label}</dt>
              </div>
            ))}
          </dl>
        </div>
      </CardContent>
    </Card>
  );
}
