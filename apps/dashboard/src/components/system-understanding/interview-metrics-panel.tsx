// Issue #309: System-level, deterministic evaluation metrics for the
// Interview / Alignment UX. This panel only presents server-calculated
// values. It never estimates missing values and never turns "unmeasured"
// into a misleading zero.

import { Activity, ShieldCheck } from "lucide-react";
import { useInterviewMetrics } from "@/api/hooks";
import type {
  InterviewMetricCategory,
  InterviewMetricKey,
  InterviewMetricOut,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp } from "@/lib/utils";

const METRIC_LABELS: Record<InterviewMetricKey, string> = {
  answers_per_understanding_update: "理解更新1回あたりの回答数",
  unknown_answer_rate: "「わからない」選択率",
  review_abandonment_rate: "レビュー途中離脱率",
  evidence_detail_expansion_rate: "根拠詳細の展開率",
  operations_per_inquiry: "疑問1件あたりの操作数",
  corrected_confirmed_intent_rate: "確認後に修正されたIntentの割合",
  incorrect_answer_confirmation_rate: "誤った回答確定率",
  runtime_contradiction_rate: "Runtime Reality Checkの矛盾発見率",
  understanding_revision_recorrection_rate: "Understanding Revisionの再修正率",
  post_approval_rejection_rate: "承認後の却下率",
  post_approval_rollback_rate: "承認後のロールバック率",
  post_approval_rejection_or_rollback_rate: "承認後の却下・ロールバック率",
  repeated_question_rate: "質問文の完全一致による再出現率",
  unchanged_item_reconfirmation_rate: "変更なし項目の再確認率",
  inquiry_resolution_rate: "疑問解消率",
  post_inquiry_confirmation_rate: "疑問解消後の明示回答完了率",
  implementation_question_transfer_rate: "実装質問のユーザー転嫁率",
  // Epic #328 Phase F (#334): 共同理解の質。効率化指標とは別カテゴリで表示する。
  joint_understanding_from_unknown_rate: "「わからない」から始まった共同理解の割合",
  joint_understanding_conclusion_rate: "共同理解が結論に至った割合",
  joint_understanding_provisional_outcome_rate: "暫定採用で終わった割合",
  joint_understanding_stale_premise_close_rate: "前提が古いまま終了した割合",
  joint_understanding_unknown_finding_rate: "「分からなかった」と記録した調査結果の割合",
  joint_understanding_reflux_rate: "回答へ転記せず理解へ反映された事実の割合",
  joint_understanding_investigation_answered_rate: "調査だけで答えに到達した割合",
  joint_understanding_developer_question_rate: "判断質問まで到達した通訳の割合",
};

const CATEGORY_LABELS: Record<InterviewMetricCategory, string> = {
  user_burden: "ユーザー負担",
  accuracy: "精度",
  ux_quality: "UX品質",
  joint_understanding: "共同理解の質",
};

// 共同理解の質は最後の独立したセクションとして並べる — 効率化指標と同じ
// グループに混ぜない(Issue #334)。
const CATEGORY_ORDER: InterviewMetricCategory[] = [
  "user_burden", "accuracy", "ux_quality", "joint_understanding",
];
const METRIC_ORDER = Object.keys(METRIC_LABELS) as InterviewMetricKey[];

function metricOrder(metric: InterviewMetricOut): number {
  const index = METRIC_ORDER.indexOf(metric.key);
  return index >= 0 ? index : METRIC_ORDER.length;
}

function formatMetricValue(metric: InterviewMetricOut): string {
  if (metric.status !== "measured" || metric.value == null) return "未計測";
  if (metric.unit === "ratio") return `${(metric.value * 100).toFixed(1)}%`;
  if (metric.unit === "answers_per_update") return `${metric.value.toFixed(2)} 回/更新`;
  return `${metric.value.toFixed(2)} 操作/疑問`;
}

function metricBasis(metric: InterviewMetricOut): string | null {
  if (metric.status !== "measured" || metric.value == null) return metric.unmeasured_reason;
  if (metric.numerator != null && metric.denominator != null) {
    return `${metric.numerator.toLocaleString("ja-JP")} / ${metric.denominator.toLocaleString("ja-JP")}`;
  }
  if (metric.sample_size > 0) return `対象 ${metric.sample_size.toLocaleString("ja-JP")}件`;
  return null;
}

function MetricCard({ metric, guardrail = false }: {
  metric: InterviewMetricOut;
  guardrail?: boolean;
}) {
  const measured = metric.status === "measured" && metric.value != null;
  const basis = metricBasis(metric);
  return (
    <div
      className={`rounded-md border p-3 space-y-2 ${guardrail ? "border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/10" : ""}`}
      data-testid={`interview-metric-${metric.key}`}
      data-status={measured ? "measured" : "unmeasured"}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug">{METRIC_LABELS[metric.key]}</p>
        <Badge variant={measured ? "outline" : "warning"}>
          {measured ? "計測済み" : "未計測"}
        </Badge>
      </div>
      <p className="text-2xl font-semibold tabular-nums" data-testid={`interview-metric-value-${metric.key}`}>
        {formatMetricValue(metric)}
      </p>
      <p className="text-xs text-muted-foreground break-words">{metric.description}</p>
      {basis && (
        <p
          className="text-xs text-muted-foreground break-words"
          data-testid={`interview-metric-basis-${metric.key}`}
        >
          {basis}
        </p>
      )}
      <details className="text-xs text-muted-foreground">
        <summary
          className="cursor-pointer text-primary underline underline-offset-2"
          data-testid={`interview-metric-definition-toggle-${metric.key}`}
        >
          定義を見る
        </summary>
        <div className="mt-2 space-y-1" data-testid={`interview-metric-definition-${metric.key}`}>
          <p><span className="font-semibold">算式:</span> {metric.formula}</p>
          <p>
            <span className="font-semibold">データ源:</span>{" "}
            {metric.sources.length > 0 ? metric.sources.join("、") : "なし"}
          </p>
        </div>
      </details>
    </div>
  );
}

export function InterviewMetricsPanel() {
  const { data, isLoading, error } = useInterviewMetrics();

  if (isLoading) {
    return (
      <Card data-testid="interview-metrics-loading">
        <CardHeader>
          <Skeleton className="h-5 w-56" />
          <Skeleton className="h-4 w-96 max-w-full" />
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card data-testid="interview-metrics-error">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Activity className="h-4 w-4" /> Interview UX 評価指標
          </CardTitle>
          <CardDescription>
            評価指標を取得できませんでした。インタビュー操作はそのまま続けられます。
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const guardrails = data.metrics.filter(metric => metric.guardrail).sort(metricOrder);
  const regularMetrics = data.metrics.filter(metric => !metric.guardrail);

  return (
    <Card data-testid="interview-metrics-panel">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="h-4 w-4" /> Interview UX 評価指標
            </CardTitle>
            <CardDescription className="mt-1">
              System全体の実測値です。算出できない値は0で補わず「未計測」と表示します。
            </CardDescription>
          </div>
          <div className="text-right text-xs text-muted-foreground">
            <p>セッション {data.sessions_observed.toLocaleString("ja-JP")}件 · イベント {data.events_observed.toLocaleString("ja-JP")}件</p>
            <p>更新 {formatTimestamp(data.generated_at)}</p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <section className="space-y-2" aria-labelledby="interview-metrics-guardrail-heading">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-amber-700" />
            <h2 id="interview-metrics-guardrail-heading" className="text-sm font-semibold">
              Guardrail 指標
            </h2>
            <Badge variant="warning">安全性・精度を監視</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            自動的な挙動変更には使わず、疑問解消と誤確定・実装質問の転嫁を個別に観測します。
          </p>
          <div className="grid gap-3 md:grid-cols-3" data-testid="interview-metrics-guardrails">
            {guardrails.map(metric => <MetricCard key={metric.key} metric={metric} guardrail />)}
          </div>
        </section>

        {CATEGORY_ORDER.map(category => {
          const items = regularMetrics
            .filter(metric => metric.category === category)
            .sort(metricOrder);
          if (items.length === 0) return null;
          return (
            <section
              key={category}
              className="space-y-2"
              data-testid={`interview-metrics-category-${category}`}
            >
              <h2 className="text-sm font-semibold">{CATEGORY_LABELS[category]}</h2>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {items.map(metric => <MetricCard key={metric.key} metric={metric} />)}
              </div>
            </section>
          );
        })}
      </CardContent>
    </Card>
  );
}
