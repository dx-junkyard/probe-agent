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
      <CardContent className="space-y-2 p-3">
        {/* 主役。ファーストビューで最も強い要素はここ 1 つだけ。
            レビュー指摘 P1: 以前はここが約 290px あり、主作業面が初期
            ビューポート (1280 x 720) の外へ押し出されていた。「次にやること」
            と CTA を 1 行に並べ、統計は既定で畳んで縦を詰める -- 主操作と
            その作業面が同時に見えないなら、この面は目的を果たしていない。 */}
        <div
          className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2"
          data-testid="cockpit-next-step"
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              <Lightbulb className="h-3.5 w-3.5" /> 次にやること
            </div>
            <h2
              className="text-base font-semibold tracking-tight break-words"
              data-testid="cockpit-next-step-title"
            >
              {model.nextStep.title}
            </h2>
            {/* 説明は 2 行まで。全文は title 属性で読める。 */}
            <p
              className="line-clamp-2 text-xs leading-5 text-muted-foreground break-words"
              title={model.nextStep.description}
            >
              {model.nextStep.description}
            </p>
          </div>
          {actionLabel && (
            <Button
              size="sm"
              className="shrink-0"
              onClick={onRunNextStep}
              data-testid="cockpit-next-step-action"
            >
              {actionLabel}
              <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </div>

        {/* 補助。完成度と件数は既定で畳む -- 常時見えている必要は無く、
            開いていると主作業面を画面外へ押し下げる。中身は DOM に残るので、
            読み上げと既存の参照はそのまま効く。 */}
        <details className="border-t pt-2" data-testid="cockpit-status-stats">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            <span data-testid="cockpit-completion-headline">
              {percent == null
                ? "理解の完成度はまだ確定できません"
                : `理解の全体像は ${percent}% まで固まっています`}
            </span>
            <span className="ml-1 underline underline-offset-2">内訳を見る</span>
          </summary>
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
        </details>
      </CardContent>
    </Card>
  );
}
