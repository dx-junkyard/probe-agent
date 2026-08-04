// Issue #356 §2: Interview status サマリー。
//
// ファーストビューで「完成度・要確認数・未設定数・次にやること」を出す。
// 数字はすべて `buildCockpitModel` が決めた値で、ここでは判定しない。

import { ArrowRight, Lightbulb } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { CockpitModel } from "./model";

export function CockpitStatusSummary({
  model,
  onGoToTopUnresolved,
}: {
  model: CockpitModel;
  /** 最優先の未解決質問へ移動する CTA。未解決が無ければ描かない。 */
  onGoToTopUnresolved: () => void;
}) {
  const top = model.unresolved[0] ?? null;
  const stats: Array<[string, number, string]> = [
    ["cockpit-stat-categories", model.categoryCount, "理解カテゴリ"],
    ["cockpit-stat-review", model.reviewCount, "要確認"],
    ["cockpit-stat-missing", model.missingCount, "未設定"],
    ["cockpit-stat-questions", model.qa.total, "質問合計"],
  ];

  return (
    <Card data-testid="cockpit-status-summary">
      <CardContent className="p-0">
        <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Interview status
                </p>
                <h2 className="mt-1 text-base font-semibold" data-testid="cockpit-completion-headline">
                  理解の全体像は {model.completionPercent}% まで固まっています
                </h2>
              </div>
              <div className="flex items-baseline gap-1">
                <span className="text-3xl font-bold tracking-tight" data-testid="cockpit-completion-percent">
                  {model.completionPercent}
                </span>
                <span className="text-sm font-semibold text-muted-foreground">%</span>
              </div>
            </div>

            <div
              className="h-2 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={model.completionPercent}
              aria-label={`インタビューの完成度 ${model.completionPercent}%`}
              data-testid="cockpit-progress-bar"
            >
              <div
                className="h-full rounded-full bg-emerald-500"
                style={{ width: `${model.completionPercent}%` }}
              />
            </div>

            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {stats.map(([testId, value, label]) => (
                <div key={testId} className="rounded-md border p-2" data-testid={testId}>
                  <dd className="text-lg font-semibold leading-none">{value}</dd>
                  <dt className="mt-1 text-xs text-muted-foreground">{label}</dt>
                </div>
              ))}
            </dl>
          </div>

          <div className="border-t p-4 lg:border-l lg:border-t-0" data-testid="cockpit-next-step">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Lightbulb className="h-4 w-4" /> 次にやること
            </div>
            <h3 className="mt-2 text-sm font-semibold">{model.nextStep.title}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground break-words">
              {model.nextStep.description}
            </p>
            {top && (
              <Button
                size="sm"
                className="mt-3 w-full"
                onClick={onGoToTopUnresolved}
                data-testid="cockpit-go-to-top-unresolved"
              >
                最優先の確認事項へ移動
                <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
