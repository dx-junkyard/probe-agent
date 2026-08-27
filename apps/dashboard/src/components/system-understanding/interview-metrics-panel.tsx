// Issue #309: System-level, deterministic evaluation metrics for the
// Interview / Alignment UX. This panel only presents server-calculated
// values. It never estimates missing values and never turns "unmeasured"
// into a misleading zero.
//
// Issue #341: the metric cards are secondary observation information, so they
// are collapsed by default behind a labelled entry point that always shows its
// state. The entry distinguishes 正常 / 要確認 / データ不足 / 取得失敗 so a bad
// value and "not judgeable yet" never share one warning. 取得失敗 is derived
// here because a server that cannot answer cannot report its own failure, and
// it never blocks the interview itself.

import { useId, useState } from "react";
import { Activity, ChevronDown, ChevronRight, ShieldCheck } from "lucide-react";
import { useInterviewMetrics } from "@/api/hooks";
import type {
  InterviewMetricAttentionOut,
  InterviewMetricCategory,
  InterviewMetricKey,
  InterviewMetricOut,
  InterviewMetricsOut,
  InterviewMetricUnit,
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
  // Issue #338: 結果 lineage から導いた質の指標。close 時のラベルではなく、
  // 「その後どうなったか」を数える。
  joint_understanding_unknown_resolution_rate: "記録した不明点が解消された割合",
  joint_understanding_hypothesis_reversal_rate: "仮説が事実によって覆された割合",
  joint_understanding_hypothesis_correction_rate: "仮説が別の仮説へ訂正された割合",
  joint_understanding_adoption_reconfirmation_rate: "暫定採用の再確認が必要になった割合",
  joint_understanding_decision_undo_rate: "判断がやり直された割合",
  joint_understanding_classification_correction_rate: "質問の分類が誤っていた割合",
  // Issue #338: 開発者の負担。質の指標とは別カテゴリで、合成しない。
  joint_understanding_rounds_per_session: "1 セッションあたりの調査ラウンド数",
  joint_understanding_developer_actions_per_session: "1 セッションあたりの操作数",
  joint_understanding_developer_findings_per_session: "1 セッションあたりの自分の記述数",
  joint_understanding_question_reask_rate: "同じ対話で再質問まで至った割合",
};

const CATEGORY_LABELS: Record<InterviewMetricCategory, string> = {
  user_burden: "ユーザー負担",
  accuracy: "精度",
  ux_quality: "UX品質",
  joint_understanding: "共同理解の利用状況",
  joint_understanding_quality: "共同理解の質(結果 lineage)",
  joint_understanding_burden: "共同理解の負担",
};

// 共同理解の質は最後の独立したセクションとして並べる — 効率化指標と同じ
// グループに混ぜない(Issue #334)。
// Issue #338: 質・負担・利用状況を独立したセクションとして並べる。効率の改善を
// 質の改善として読ませないために、同じグループへ混ぜない。
const CATEGORY_ORDER: InterviewMetricCategory[] = [
  "user_burden", "accuracy", "ux_quality", "joint_understanding",
  "joint_understanding_quality", "joint_understanding_burden",
];
const METRIC_ORDER = Object.keys(METRIC_LABELS) as InterviewMetricKey[];

function metricOrder(metric: InterviewMetricOut): number {
  const index = METRIC_ORDER.indexOf(metric.key);
  return index >= 0 ? index : METRIC_ORDER.length;
}

function byMetricOrder(a: InterviewMetricOut, b: InterviewMetricOut): number {
  return metricOrder(a) - metricOrder(b);
}

// Issue #338: one suffix per unit, matched exhaustively. The previous fallthrough
// labelled anything that was not a ratio 「操作/疑問」, so a new unit would have
// been rendered with another unit's name — the same "one displayed word carrying
// two facts" defect #366 exists to remove.
const UNIT_SUFFIX: Record<InterviewMetricUnit, string> = {
  ratio: "%",
  answers_per_update: " 回/更新",
  operations_per_inquiry: " 操作/疑問",
  per_session: " 件/セッション",
};

function formatMetricValue(metric: InterviewMetricOut): string {
  if (metric.status !== "measured" || metric.value == null) return "未計測";
  if (metric.unit === "ratio") {
    return `${(metric.value * 100).toFixed(1)}${UNIT_SUFFIX.ratio}`;
  }
  return `${metric.value.toFixed(2)}${UNIT_SUFFIX[metric.unit]}`;
}

function formatThresholdValue(metric: InterviewMetricOut, threshold: number): string {
  if (metric.unit === "ratio") return `${(threshold * 100).toFixed(1)}%`;
  return threshold.toFixed(2);
}

function metricBasis(metric: InterviewMetricOut): string | null {
  if (metric.status !== "measured" || metric.value == null) return metric.unmeasured_reason;
  if (metric.numerator != null && metric.denominator != null) {
    return `${metric.numerator.toLocaleString("ja-JP")} / ${metric.denominator.toLocaleString("ja-JP")}`;
  }
  if (metric.sample_size > 0) return `対象 ${metric.sample_size.toLocaleString("ja-JP")}件`;
  return null;
}

// --- Issue #341: entry-point state ------------------------------------------

type EntryState = "normal" | "attention" | "insufficient_data" | "unavailable";

const ENTRY_STATE_LABELS: Record<EntryState, string> = {
  normal: "正常",
  attention: "要確認",
  insufficient_data: "データ不足",
  unavailable: "取得失敗",
};

// 「値が悪い」と「まだ判断できない」は同じ警告表現にしない。
const ENTRY_STATE_VARIANTS: Record<EntryState, "outline" | "destructive" | "secondary" | "warning"> = {
  normal: "outline",
  attention: "destructive",
  insufficient_data: "secondary",
  unavailable: "warning",
};

function entryStateText(state: EntryState, attentionCount: number): string {
  if (state === "attention") {
    return `${ENTRY_STATE_LABELS.attention} ${attentionCount.toLocaleString("ja-JP")}件`;
  }
  return ENTRY_STATE_LABELS[state];
}

const ATTENTION_STATE_LABELS: Record<InterviewMetricAttentionOut["state"], string> = {
  attention: "要確認",
  ok: "判定内",
  insufficient_data: "データ不足",
  not_measurable: "計測手段なし",
  criterion_unset: "判定条件未設定",
  observation_only: "定期観測",
};

function attentionCriterionText(metric: InterviewMetricOut): string {
  const attention = metric.attention;
  if (!attention.watched) return "通知対象ではありません(定期観測)";
  if (attention.threshold == null) {
    return "閾値は実データを確認して別途決定します";
  }
  const boundary = formatThresholdValue(metric, attention.threshold);
  const direction = attention.direction === "high_is_bad" ? "を上回る" : "を下回る";
  return `判定条件: ${boundary} ${direction}`;
}

function attentionScopeText(metric: InterviewMetricOut): string {
  const attention = metric.attention;
  const period = attention.window === "all_time" ? "全期間" : "未設定";
  const sample = metric.sample_size.toLocaleString("ja-JP");
  const minimum = attention.min_sample.toLocaleString("ja-JP");
  return `対象期間: ${period} · 母数: ${sample}件(最低 ${minimum}件)`;
}

// --- Cards -------------------------------------------------------------------

function MetricCard({ metric, guardrail = false }: {
  metric: InterviewMetricOut;
  guardrail?: boolean;
}) {
  const measured = metric.status === "measured" && metric.value != null;
  const basis = metricBasis(metric);
  const attention = metric.attention;
  return (
    <div
      className={`rounded-md border p-3 space-y-2 ${guardrail ? "border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/10" : ""}`}
      data-testid={`interview-metric-${metric.key}`}
      data-status={measured ? "measured" : "unmeasured"}
      data-attention={attention.state}
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
      {attention.watched && (
        <p
          className="text-xs text-muted-foreground break-words"
          data-testid={`interview-metric-attention-${metric.key}`}
        >
          {ATTENTION_STATE_LABELS[attention.state]} · {attentionCriterionText(metric)}
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

function AttentionItem({ metric }: { metric: InterviewMetricOut }) {
  const direction = metric.attention.direction === "high_is_bad" ? "高すぎます" : "低すぎます";
  return (
    <div
      className="rounded-md border border-destructive/50 bg-destructive/5 p-3 space-y-1"
      data-testid={`interview-metrics-attention-item-${metric.key}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug">{METRIC_LABELS[metric.key]}</p>
        <Badge variant="destructive">要確認</Badge>
      </div>
      <p className="text-sm">
        現在値 <span className="font-semibold tabular-nums">{formatMetricValue(metric)}</span> は
        判定条件に対して{direction}。
      </p>
      <p className="text-xs text-muted-foreground break-words">{metric.description}</p>
      <p className="text-xs text-muted-foreground">{attentionCriterionText(metric)}</p>
      <p className="text-xs text-muted-foreground">{attentionScopeText(metric)}</p>
    </div>
  );
}

function EvaluabilitySection({ data }: { data: InterviewMetricsOut }) {
  const unmeasured = data.metrics.filter(metric => metric.status !== "measured");
  const undersampled = data.metrics.filter(
    metric => metric.attention.state === "insufficient_data",
  ).sort(byMetricOrder);
  return (
    <section className="space-y-2" data-testid="interview-metrics-evaluability">
      <h2 className="text-sm font-semibold">データの評価可能性</h2>
      <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 text-sm">
        <div className="rounded-md border p-3">
          <dt className="text-xs text-muted-foreground">観測セッション数</dt>
          <dd className="text-lg font-semibold tabular-nums" data-testid="interview-metrics-sessions-observed">
            {data.sessions_observed.toLocaleString("ja-JP")}件
          </dd>
        </div>
        <div className="rounded-md border p-3">
          <dt className="text-xs text-muted-foreground">観測イベント数</dt>
          <dd className="text-lg font-semibold tabular-nums" data-testid="interview-metrics-events-observed">
            {data.events_observed.toLocaleString("ja-JP")}件
          </dd>
        </div>
        <div className="rounded-md border p-3">
          <dt className="text-xs text-muted-foreground">未計測の指標</dt>
          <dd className="text-lg font-semibold tabular-nums" data-testid="interview-metrics-unmeasured-count">
            {unmeasured.length.toLocaleString("ja-JP")} / {data.metrics.length.toLocaleString("ja-JP")}
          </dd>
        </div>
        <div className="rounded-md border p-3">
          <dt className="text-xs text-muted-foreground">最終更新</dt>
          <dd className="text-sm" data-testid="interview-metrics-generated-at">
            {formatTimestamp(data.generated_at)}
          </dd>
        </div>
      </dl>
      {undersampled.length > 0 && (
        <div className="text-xs text-muted-foreground space-y-1" data-testid="interview-metrics-undersampled">
          <p className="font-semibold">判定に必要な母数が不足している指標</p>
          <ul className="list-disc pl-5 space-y-0.5">
            {undersampled.map(metric => (
              <li key={metric.key}>
                {METRIC_LABELS[metric.key]} — {attentionScopeText(metric)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        判定ポリシー {data.attention.policy_version} · 監視対象 {data.attention.watched_count.toLocaleString("ja-JP")}件
      </p>
    </section>
  );
}

// --- Panel -------------------------------------------------------------------

export function InterviewMetricsPanel() {
  const { data, isLoading, isPending, fetchStatus, error } = useInterviewMetrics();
  const [open, setOpen] = useState(false);
  const detailsId = useId();

  const entryState: EntryState = error || !data ? "unavailable" : data.attention.state;
  const attentionCount = data?.attention.attention_count ?? 0;

  // The metrics are System-scoped, so with no System selected the query never
  // runs. That is not a fetch failure and must not be shown as 取得失敗.
  if (isPending && fetchStatus === "idle" && !data) return null;

  if (isLoading) {
    return (
      <div data-testid="interview-metrics-loading" className="flex items-center gap-2">
        <Skeleton className="h-9 w-56" />
      </div>
    );
  }

  const attentionMetrics = (data?.metrics ?? [])
    .filter(metric => metric.attention.state === "attention")
    .sort(byMetricOrder);
  const guardrails = (data?.metrics ?? []).filter(metric => metric.guardrail).sort(byMetricOrder);
  const regularMetrics = (data?.metrics ?? []).filter(metric => !metric.guardrail);

  return (
    <div className="space-y-3" data-testid="interview-metrics-section">
      <button
        type="button"
        onClick={() => setOpen(current => !current)}
        aria-expanded={open}
        aria-controls={detailsId}
        data-testid="interview-metrics-entry"
        data-state={entryState}
        className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm hover:bg-accent focus:outline-none focus:ring-2 focus:ring-ring"
      >
        {open
          ? <ChevronDown className="h-4 w-4" aria-hidden="true" />
          : <ChevronRight className="h-4 w-4" aria-hidden="true" />}
        <Activity className="h-4 w-4" aria-hidden="true" />
        <span>評価指標</span>
        <span aria-hidden="true">·</span>
        <Badge variant={ENTRY_STATE_VARIANTS[entryState]} data-testid="interview-metrics-entry-state">
          {entryStateText(entryState, attentionCount)}
        </Badge>
        <span className="sr-only">
          {open ? "評価指標の詳細を閉じる" : "評価指標の詳細を開く"}
        </span>
      </button>

      {open && (entryState === "unavailable" || !data) && (
        <Card id={detailsId} data-testid="interview-metrics-error">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="h-4 w-4" /> Interview UX 評価指標
            </CardTitle>
            <CardDescription>
              評価指標を取得できませんでした。インタビュー操作はそのまま続けられます。
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {open && data && entryState !== "unavailable" && (
        <Card
          id={detailsId}
          role="region"
          aria-label="Interview UX 評価指標"
          data-testid="interview-metrics-panel"
        >
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
            <section className="space-y-2" data-testid="interview-metrics-attention-section">
              <h2 className="text-sm font-semibold">要確認事項</h2>
              {attentionMetrics.length === 0 ? (
                <p className="text-xs text-muted-foreground" data-testid="interview-metrics-attention-empty">
                  {data.attention.state === "insufficient_data"
                    ? "要確認の指標はありません。判定に必要なデータがまだ足りない指標があります。"
                    : "要確認の指標はありません。"}
                </p>
              ) : (
                <div className="grid gap-3 md:grid-cols-2">
                  {attentionMetrics.map(metric => (
                    <AttentionItem key={metric.key} metric={metric} />
                  ))}
                </div>
              )}
            </section>

            <EvaluabilitySection data={data} />

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
                .sort(byMetricOrder);
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
      )}
    </div>
  );
}
