// Issues #468/#470/#471/#472 (Epic #467): the grounded-answer, failure and
// 参照範囲 (coverage) displays of the AI assistant. Pure rendering of what the
// server already decided -- nothing here re-derives a grounding verdict, a
// failure class or a coverage state (docs/01-specifications/capabilities/
// assistant-answer-quality.md §3/§4/§5).
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Wrench } from "lucide-react";
import { useAskMetric } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import type {
  AnswerPoint, AskCoverageEntry, AskContextManifest, AssistantCitation,
  AssistantFailure, AssistantAskOut, GroundingState, RecoveryKind,
} from "@/api/types";

// ── source chips ────────────────────────────────────────────────────

export function CitationChip({ citation }: { citation: AssistantCitation }) {
  if (citation.type === "setting") {
    return (
      <code
        className="text-[11px] bg-muted px-1.5 py-0.5 rounded font-mono"
        title={citation.title}
        data-testid="assistant-citation"
      >
        {citation.id}
      </code>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-[11px] bg-muted px-1.5 py-0.5 rounded"
      title={citation.detail || citation.id}
      data-testid="assistant-citation"
    >
      {citation.type === "diagnostic_check" ? (
        <Wrench className="h-3 w-3" />
      ) : (
        <ArrowRight className="h-3 w-3" />
      )}
      {citation.title || citation.id}
    </span>
  );
}

// ── grounded points ─────────────────────────────────────────────────

type PointGroupKey = "facts" | "interpretations" | "stale" | "unsupported" | "unknowns";

/** The server decided each point's `grounding`; this only chooses the group.
 * Grounding outranks kind, and a fact that is not `supported` is never shown
 * under 確認済みの事実 (a `not_required` fact is an unverified claim too). */
function pointGroup(point: AnswerPoint): PointGroupKey {
  if (point.grounding === "unsupported") return "unsupported";
  if (point.grounding === "stale") return "stale";
  if (point.kind === "fact") return point.grounding === "supported" ? "facts" : "unsupported";
  if (point.kind === "interpretation") return "interpretations";
  return "unknowns";
}

const GROUP_ORDER: PointGroupKey[] = ["facts", "interpretations", "stale", "unsupported", "unknowns"];

const GROUP_HEADING: Record<PointGroupKey, string> = {
  facts: "確認済みの事実",
  interpretations: "解釈・提案",
  stale: "古い根拠に基づく内容（再確認が必要）",
  unsupported: "根拠を確認できなかった主張（事実として扱わないでください）",
  unknowns: "分かっていないこと",
};

const GROUNDING_BANNER: Partial<Record<GroundingState, string>> = {
  partially_grounded: "この回答の一部は、根拠を確認できませんでした。該当する項目を事実として扱わないでください。",
  ungrounded: "この回答は、根拠を確認できていません。事実として扱わず、画面のデータで確認してください。",
};

function PointSources({ point, citations }: { point: AnswerPoint; citations: AssistantCitation[] }) {
  if (point.sources.length === 0) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {point.sources.map((s, i) => {
        const found = citations.find((c) => c.type === s.type && c.id === s.id);
        const citation: AssistantCitation =
          found ?? ({ type: s.type as AssistantCitation["type"], id: s.id, title: s.id, detail: "" });
        return <CitationChip key={`${s.type}-${s.id}-${i}`} citation={citation} />;
      })}
    </span>
  );
}

export function StructuredAnswer({ result }: { result: AssistantAskOut }) {
  const points = result.points ?? [];
  const missing = result.missing_information ?? [];
  const banner = result.grounding_state ? GROUNDING_BANNER[result.grounding_state] : undefined;
  return (
    <div className="space-y-2" data-testid="assistant-structured-answer">
      <p className="text-sm font-medium whitespace-pre-wrap" data-testid="assistant-conclusion">
        {result.conclusion}
      </p>
      {banner && (
        <p
          role="status"
          className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100"
          data-testid="assistant-grounding-banner"
        >
          {banner}
        </p>
      )}
      {GROUP_ORDER.map((key) => {
        const items = points.filter((p) => pointGroup(p) === key);
        if (items.length === 0) return null;
        const unsupported = key === "unsupported";
        return (
          <section
            key={key}
            data-testid={`assistant-points-${key}`}
            className={unsupported
              ? "rounded border border-amber-400 bg-amber-50 p-2 space-y-1 dark:bg-amber-950"
              : "space-y-1"}
          >
            <h4 className={`text-[11px] font-medium ${unsupported ? "text-amber-900 dark:text-amber-100" : "text-muted-foreground"}`}>
              {unsupported && <AlertTriangle className="mr-1 inline h-3 w-3" aria-hidden="true" />}
              {unsupported && <span>【未確認】</span>}
              {GROUP_HEADING[key]}
            </h4>
            <ul className="list-disc pl-4 space-y-1">
              {items.map((p, i) => (
                <li key={i} className="text-sm">
                  {p.text}
                  <PointSources point={p} citations={result.citations} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      {missing.length > 0 && (
        <section className="space-y-1" data-testid="assistant-missing-information">
          <h4 className="text-[11px] font-medium text-muted-foreground">不足している情報</h4>
          <ul className="list-disc pl-4 space-y-1">
            {missing.map((m, i) => (
              <li key={i} className="text-sm">
                {m.what}
                {m.how_to_get && (
                  <span className="block text-xs text-muted-foreground">確認方法: {m.how_to_get}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// ── failure / recovery ──────────────────────────────────────────────

export function RecoveryActions({
  failure, onRetry, onRephrase, pending,
}: {
  failure: AssistantFailure | null | undefined;
  onRetry?: () => void;
  onRephrase?: () => void;
  pending?: boolean;
}) {
  const navigate = useNavigate();
  const recovery = failure?.recovery ?? [];
  if (recovery.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2" data-testid="assistant-recovery">
      {recovery.map((r, i) => {
        const kind: RecoveryKind = r.kind;
        if (kind === "retry") {
          if (!onRetry) return null;
          return (
            <Button
              key={i} type="button" size="sm" variant="outline" disabled={pending}
              onClick={onRetry} data-testid="assistant-recovery-retry"
            >
              {r.label}
            </Button>
          );
        }
        if (kind === "rephrase") {
          if (!onRephrase) return null;
          return (
            <Button
              key={i} type="button" size="sm" variant="outline"
              onClick={onRephrase} data-testid="assistant-recovery-rephrase"
            >
              {r.label}
            </Button>
          );
        }
        // configure / open_settings: both lead to the settings screen; the
        // setting key (when the target is one) is shown, not navigated to.
        const isPath = !!r.target && r.target.startsWith("/");
        return (
          <span key={i} className="inline-flex items-center gap-1.5">
            <Button
              type="button" size="sm" variant="outline"
              onClick={() => navigate(isPath ? r.target! : "/settings")}
              data-testid={`assistant-recovery-${kind}`}
            >
              {r.label}
            </Button>
            {r.target && !isPath && (
              <code className="bg-muted px-1 py-0.5 rounded font-mono text-[11px]" data-testid="assistant-recovery-key">
                {r.target}
              </code>
            )}
          </span>
        );
      })}
    </div>
  );
}

export function FailureCard({
  message, failure, onRetry, onRephrase, readOnly, pending,
}: {
  message: string;
  failure?: AssistantFailure | null;
  onRetry?: () => void;
  onRephrase?: () => void;
  readOnly?: boolean;
  pending?: boolean;
}) {
  return (
    <div
      role="alert"
      className="rounded border border-destructive/40 bg-destructive/5 p-2 space-y-2"
      data-testid="assistant-failure-card"
    >
      <p className="text-sm font-medium text-destructive">回答を作成できませんでした</p>
      <p className="text-sm whitespace-pre-wrap" data-testid="assistant-failure-message">
        {failure?.message || message}
      </p>
      {readOnly ? (
        <p className="text-xs text-muted-foreground" data-testid="assistant-failure-readonly">
          過去の失敗の記録です。もう一度質問する場合は、入力欄から送信してください。
        </p>
      ) : (
        <RecoveryActions failure={failure} onRetry={onRetry} onRephrase={onRephrase} pending={pending} />
      )}
    </div>
  );
}

export function DeterministicNotice({
  failure, onRetry, onRephrase, readOnly, pending,
}: {
  failure?: AssistantFailure | null;
  onRetry?: () => void;
  onRephrase?: () => void;
  readOnly?: boolean;
  pending?: boolean;
}) {
  return (
    <div
      role="status"
      className="rounded border border-amber-300 bg-amber-50 p-2 space-y-2 text-amber-900 dark:bg-amber-950 dark:text-amber-100"
      data-testid="assistant-deterministic-notice"
    >
      <p className="text-xs">
        LLM を使用できなかったため、定型の回答を表示しています。
        {failure?.message && <span className="block mt-0.5">{failure.message}</span>}
      </p>
      {!readOnly && (
        <RecoveryActions failure={failure} onRetry={onRetry} onRephrase={onRephrase} pending={pending} />
      )}
    </div>
  );
}

// ── 参照範囲 ────────────────────────────────────────────────────────

const SOURCE_TYPE_LABEL: Record<string, string> = {
  screen_data: "画面データ",
  state_item: "状態項目",
  diagnostic_check: "診断チェック",
  setting: "設定",
  pipeline_step: "パイプライン手順",
  ui_draft: "未保存の下書き",
  related_context: "関連情報",
};

const FRESHNESS_LABEL: Record<string, string> = {
  current: "最新",
  stale: "古い",
  unknown: "鮮度不明",
  not_tracked: "鮮度は追跡対象外",
};

const OMIT_REASON_LABEL: Record<string, string> = {
  budget: "入力の上限のため省略",
  stale_target: "対象が古いため省略",
  unsupported: "未対応のため取得せず",
  unavailable: "取得できず",
};

const COVERAGE_STATE_LABEL: Record<string, string> = {
  complete: "全件",
  truncated: "切り詰め",
  empty: "0 件",
  unavailable: "取得できず",
  unsupported: "未対応",
};

const STAGE_LABEL: Record<string, string> = {
  request_validation: "リクエスト検証",
  diagnostics: "診断",
  system_state: "System の状態",
  screen_context: "画面コンテキスト",
  context_bundle: "関連情報の取得",
  context_pack: "コンテキスト構築",
  llm: "LLM 呼び出し",
  response_validation: "回答の検証",
  persist: "保存",
};

/** One coverage line. `total: null` is 「総数不明」 -- never guessed, never 0. */
export function describeCoverage(c: AskCoverageEntry): string {
  const total = c.total == null ? "総数不明" : `全 ${c.total} 件`;
  switch (c.state) {
    case "truncated":
      return c.limit != null
        ? `上限 ${c.limit} 件で切り詰め（${total}、表示 ${c.returned} 件）`
        : `切り詰め（${total}、表示 ${c.returned} 件）`;
    case "complete":
      return `全件を参照（${c.returned} 件）`;
    case "empty":
      return "読めて 0 件";
    case "unavailable":
      return "読めませんでした（0 件とは限りません）";
    case "unsupported":
      return "この項目は対象外です";
    default:
      return `${COVERAGE_STATE_LABEL[c.state] ?? c.state}（${c.returned} 件）`;
  }
}

function ManifestBody({ manifest, stageTimings }: {
  manifest: AskContextManifest;
  stageTimings: Record<string, { ms: number; count: number }>;
}) {
  const sources = manifest.sources ?? [];
  const coverage = manifest.coverage ?? [];
  const omitted = manifest.omitted_sections ?? [];
  const stages = Object.entries(stageTimings);
  const nothing = sources.length === 0 && coverage.length === 0 && omitted.length === 0
    && !manifest.budget && manifest.history_turns == null && stages.length === 0;
  if (nothing) {
    return <p className="text-xs text-muted-foreground">この回答には参照範囲の記録がありません。</p>;
  }
  return (
    <div className="space-y-2 text-xs" data-testid="assistant-scope-body">
      {sources.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">参照したもの</p>
          <ul className="list-disc pl-4">
            {sources.map((s, i) => (
              <li key={i} data-testid="assistant-scope-source">
                {SOURCE_TYPE_LABEL[s.type] ?? s.type}: <code className="font-mono">{s.id}</code>
                （{FRESHNESS_LABEL[s.freshness ?? "unknown"] ?? s.freshness}）
              </li>
            ))}
          </ul>
        </div>
      )}
      {coverage.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">一覧の範囲</p>
          <ul className="list-disc pl-4">
            {coverage.map((c, i) => (
              <li key={i} data-testid="assistant-scope-coverage">
                <code className="font-mono">{c.section}</code>: {describeCoverage(c)}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">切り詰められた一覧に無いことは、存在しないという意味ではありません。</p>
        </div>
      )}
      {omitted.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">省略した項目</p>
          <ul className="list-disc pl-4">
            {omitted.map((o, i) => (
              <li key={i} data-testid="assistant-scope-omitted">
                <code className="font-mono">{o.section}</code>: {OMIT_REASON_LABEL[o.reason] ?? o.reason}
                {o.dropped_items != null && `（${o.dropped_items} 件）`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {manifest.budget?.over_budget && (
        <p className="text-amber-700" data-testid="assistant-scope-over-budget">
          入力の上限を超過しました（{manifest.budget.used_chars} / {manifest.budget.limit_chars} 文字）。
        </p>
      )}
      {manifest.history_turns != null && (
        <p>参照した過去の発言: {manifest.history_turns} 件</p>
      )}
      {stages.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">処理時間</p>
          <ul className="list-disc pl-4">
            {stages.map(([name, t]) => (
              <li key={name} data-testid="assistant-scope-stage">
                {STAGE_LABEL[name] ?? name}: {Math.round(t.ms)} ms
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Lazy: nothing is fetched until the developer opens it. A failed fetch is
 * shown as a failure with a retry, never as an empty list (#466). */
export function ScopeDisclosure({ requestId }: { requestId: string }) {
  const [opened, setOpened] = useState(false);
  const metric = useAskMetric(requestId, opened);
  return (
    <details className="text-xs" data-testid="assistant-scope">
      <summary
        className="cursor-pointer text-muted-foreground"
        onClick={() => setOpened(true)}
      >
        参照範囲
      </summary>
      <div className="mt-1">
        {metric.isError ? (
          <div className="space-y-1" data-testid="assistant-scope-error">
            <p className="text-amber-700">参照範囲を取得できませんでした。</p>
            <button
              type="button"
              className="text-xs text-primary hover:underline cursor-pointer"
              data-testid="assistant-scope-retry"
              onClick={() => void metric.refetch()}
            >
              再試行
            </button>
          </div>
        ) : metric.data ? (
          <ManifestBody manifest={metric.data.manifest ?? {}} stageTimings={metric.data.stage_timings ?? {}} />
        ) : opened ? (
          <p className="text-muted-foreground" data-testid="assistant-scope-loading">読み込み中…</p>
        ) : null}
      </div>
    </details>
  );
}
