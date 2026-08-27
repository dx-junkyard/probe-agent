// Issue #283: 「現在の理解」の平易な段階表示。
//
// interview.tsx の UnderstandingPanel (旧・単一フラットリスト) は、内部状態
// (confidence.level 等の canonical enum)・説明・根拠を一度に出しており、
// 何が分かっていて/何をユーザーに確認してほしいのかが読み取りにくかった。
// この UnderstandingOverview は初期表示を
//   「分かったこと」(confidence が confirmed/likely の項目)
//   「確認したいこと」(confidence が uncertain/conflicting の項目 +
//                       gap_analysis + open_questions)
//   「次にすること」(呼び出し側の next-action ロジックをそのまま表示)
// の3グループのサマリーに絞り、根拠・確信度の理由・schema上の詳細フィールド
// (evidence / related_docs / related_apis / children / hypothesis /
// answer_options 等)はアイテムごとに「根拠を見る」で折りたたむ。
//
// canonical enum の値そのものはデータ上変更しない。表示ラベルへの変換は
// このファイル内の単一マッピングテーブル (CONFIDENCE_LABELS 等) だけを通す。
// 未知の値が来ても生の enum 文字列を画面に出さず、安全側 (要確認寄り) の
// 既定ラベルにフォールバックする。
//
// 要対応(action-required)と参考情報(informational)の区別は、色だけに
// 依存せず、視覚的に隠したテキスト (sr-only) と `data-action-required`
// 属性の両方で伝える。
//
// この変更は表示のみ: 理解生成のロジック・API・スキーマは変更しない。

import { useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CurrentUnderstanding, GapItem, OpenQuestion, UnderstandingItem } from "@/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

// ── 表示ラベルの単一マッピングテーブル (canonical enum → 日本語) ──────

const CONFIDENCE_LABELS: Record<string, string> = {
  confirmed: "確認済み",
  likely: "おそらく正しい",
  uncertain: "未確認",
  conflicting: "矛盾あり",
};

function confidenceLabel(level: string): string {
  return CONFIDENCE_LABELS[level] ?? "未確認";
}

// confirmed / likely は参考情報として提示する。それ以外 (uncertain /
// conflicting / 未知の値) は安全側に倒し、ユーザー確認が必要な項目として扱う。
function confidenceNeedsConfirmation(level: string): boolean {
  return level !== "confirmed" && level !== "likely";
}

function confidenceBadgeVariant(level: string): BadgeVariant {
  if (level === "confirmed") return "success";
  if (level === "likely") return "secondary";
  if (level === "conflicting") return "destructive";
  return "outline";
}

const SEVERITY_LABELS: Record<string, string> = {
  high: "重大",
  medium: "中程度",
  low: "軽微",
};

function severityLabel(value: string): string {
  return SEVERITY_LABELS[value] ?? "軽微";
}

function severityBadgeVariant(value: string): BadgeVariant {
  if (value === "high") return "destructive";
  if (value === "medium") return "warning";
  return "outline";
}

const GAP_TYPE_LABELS: Record<string, string> = {
  docs_only: "ドキュメントのみに記載",
  code_only: "コードのみに存在",
  source_doc_mismatch: "コードとドキュメントの不一致",
  stale_explanation: "説明が古い可能性",
  ambiguous_ownership: "責務があいまい",
  unclassified_entrypoint: "未分類のエントリポイント",
  missing_probe_flow: "計測対象フロー未特定",
};

function gapTypeLabel(value: string): string {
  return GAP_TYPE_LABELS[value] ?? "確認が必要な差分";
}

const PRIORITY_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function priorityLabel(value: string): string {
  return PRIORITY_LABELS[value] ?? "中";
}

function priorityBadgeVariant(value: string): BadgeVariant {
  if (value === "high") return "destructive";
  if (value === "low") return "outline";
  return "warning";
}

const CATEGORY_LABELS: Record<keyof CurrentUnderstanding, string> = {
  // Issue #352: Vision は Purpose と別の主張。全文ツリーでも独立した節にする。
  vision: "Vision",
  system_purpose: "システムの目的",
  core_capabilities: "主要機能",
  capability_elements: "機能要素",
  supporting_elements: "支援要素",
  api_boundaries: "API境界",
  probe_flow_candidates: "プローブ対象フロー候補",
};

const UNDERSTANDING_SECTIONS: (keyof CurrentUnderstanding)[] = [
  "vision",
  "system_purpose",
  "core_capabilities",
  "capability_elements",
  "supporting_elements",
  "api_boundaries",
  "probe_flow_candidates",
];

type CategorizedItem = {
  key: string;
  category: string;
  item: UnderstandingItem;
};

function collectItems(understanding: CurrentUnderstanding | null | undefined): CategorizedItem[] {
  if (!understanding) return [];
  const out: CategorizedItem[] = [];
  UNDERSTANDING_SECTIONS.forEach(field => {
    (understanding[field] ?? []).forEach((item, idx) => {
      out.push({ key: `${field}-${idx}`, category: CATEGORY_LABELS[field], item });
    });
  });
  return out;
}

// ── 個々のアイテムカード ────────────────────────────────────────────
// heading + summary + (要対応 or 参考情報) バッジをまず見せ、根拠・理由・
// schema上の詳細は `detail` に渡した内容としてトグルの中に隠す。

function OverviewItemCard({
  id, heading, summary, badgeLabel, badgeVariant, actionRequired, detail,
}: {
  id: string;
  heading: string;
  summary?: ReactNode;
  badgeLabel: string;
  badgeVariant: BadgeVariant;
  actionRequired: boolean;
  detail?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const detailId = `overview-item-detail-${id}`;

  return (
    <div
      className="rounded-md border p-3 space-y-2"
      data-testid={`overview-item-${id}`}
      data-action-required={actionRequired}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium text-sm break-words">{heading}</div>
          {summary && <p className="text-xs text-muted-foreground mt-0.5 break-words">{summary}</p>}
        </div>
        <Badge
          variant={badgeVariant}
          className="shrink-0 gap-1"
          data-testid={`overview-item-badge-${id}`}
        >
          {actionRequired ? (
            <AlertCircle className="h-3 w-3" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
          )}
          {/* 色だけに頼らず、スクリーンリーダー向けに要対応/参考情報を明示する */}
          <span className="sr-only">{actionRequired ? "要確認: " : "参考情報: "}</span>
          {badgeLabel}
        </Badge>
      </div>
      {detail && (
        <div>
          <button
            type="button"
            className="text-xs text-primary underline underline-offset-2"
            onClick={() => setOpen(o => !o)}
            aria-expanded={open}
            aria-controls={detailId}
            data-testid={`overview-item-toggle-${id}`}
          >
            {open ? "根拠を隠す" : "根拠を見る"}
          </button>
          {open && (
            <div
              id={detailId}
              className="mt-2 space-y-1 text-[11px] text-muted-foreground"
              data-testid={detailId}
            >
              {detail}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EvidenceList({ evidence }: {
  evidence: { path: string; start_line: number; end_line: number; summary?: string }[];
}) {
  if (evidence.length === 0) return null;
  return (
    <div className="space-y-0.5">
      <p className="font-semibold">根拠コード:</p>
      {evidence.map((e, i) => (
        <p key={i} className="font-mono">
          {e.path}:{e.start_line}-{e.end_line}
          {e.summary ? ` — ${e.summary}` : ""}
        </p>
      ))}
    </div>
  );
}

function understandingItemDetail(item: UnderstandingItem): ReactNode | undefined {
  const hasDetail =
    !!item.confidence.reason ||
    !!item.why_core ||
    item.evidence.length > 0 ||
    item.related_apis.length > 0 ||
    item.related_docs.length > 0 ||
    item.children.length > 0;
  if (!hasDetail) return undefined;
  return (
    <>
      {item.confidence.reason && (
        <p><span className="font-semibold">確信度の理由: </span>{item.confidence.reason}</p>
      )}
      {item.why_core && <p className="italic">{item.why_core}</p>}
      <EvidenceList evidence={item.evidence} />
      {item.related_apis.length > 0 && <p>関連API: {item.related_apis.join("、")}</p>}
      {item.related_docs.length > 0 && <p>関連ドキュメント: {item.related_docs.join("、")}</p>}
      {item.children.length > 0 && <p>子要素: {item.children.join("、")}</p>}
    </>
  );
}

function questionDetail(q: OpenQuestion): ReactNode | undefined {
  const evidenceRefs = q.evidence_refs ?? [];
  const answerOptions = q.answer_options ?? [];
  const hasDetail = !!q.hypothesis || evidenceRefs.length > 0 || answerOptions.length > 0;
  if (!hasDetail) return undefined;
  return (
    <>
      {q.hypothesis && <p><span className="font-semibold">仮説: </span>{q.hypothesis}</p>}
      <EvidenceList evidence={evidenceRefs} />
      {answerOptions.length > 0 && <p>回答候補: {answerOptions.join("、")}</p>}
    </>
  );
}

// ── グループ (分かったこと / 確認したいこと / 次にすること) ──────────

function OverviewGroup({ title, testid, children }: {
  title: string;
  testid: string;
  children: ReactNode;
}) {
  const headingId = `${testid}-heading`;
  return (
    <section aria-labelledby={headingId} data-testid={testid} className="space-y-2">
      <h4 id={headingId} className="text-xs font-semibold uppercase text-muted-foreground">
        {title}
      </h4>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function EmptyGroupNote({ text }: { text: string }) {
  return <p className="text-xs text-muted-foreground" data-testid="overview-group-empty">{text}</p>;
}

// ── 「詳細をすべて見る」: カテゴリ別の全項目一覧 (旧 UnderstandingPanel 相当) ──
// 折りたたみ既定。確信度バッジは翻訳済みラベルのみを表示し、canonical enum
// の生文字列は出さない。

function UnderstandingCategoryDetail({ understanding }: { understanding: CurrentUnderstanding }) {
  const sections = UNDERSTANDING_SECTIONS
    .map(field => [CATEGORY_LABELS[field], understanding[field] ?? []] as const)
    .filter(([, items]) => items.length > 0);

  if (sections.length === 0) return null;

  return (
    <div className="space-y-4">
      {sections.map(([label, items]) => (
        <div key={label}>
          <h5 className="text-xs font-semibold uppercase text-muted-foreground mb-2">{label}</h5>
          <div className="space-y-2">
            {items.map((item, idx) => (
              <div key={idx} className="rounded-md border p-3 space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium text-sm break-words">{item.name}</div>
                    {item.summary && <p className="text-xs break-words">{item.summary}</p>}
                  </div>
                  <Badge variant={confidenceBadgeVariant(item.confidence.level)} className="shrink-0">
                    {confidenceLabel(item.confidence.level)}
                  </Badge>
                </div>
                {item.confidence.reason && (
                  <p className="text-[11px] text-muted-foreground">{item.confidence.reason}</p>
                )}
                {item.evidence.length > 0 && (
                  <div className="space-y-1">
                    {item.evidence.map((e, i) => (
                      <div key={i} className="text-[10px] text-muted-foreground font-mono">
                        {e.path}:{e.start_line}-{e.end_line} — {e.summary}
                      </div>
                    ))}
                  </div>
                )}
                {item.related_apis.length > 0 && (
                  <div className="text-[10px] text-muted-foreground">
                    APIs: {item.related_apis.join(", ")}
                  </div>
                )}
                {item.children.length > 0 && (
                  <div className="text-[10px] text-muted-foreground">
                    子要素: {item.children.join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyOverviewGuidance() {
  return (
    <div className="text-sm text-muted-foreground text-center py-6 space-y-1" data-testid="understanding-overview-empty">
      <p>理解項目はまだありません。</p>
      <p className="text-xs">
        「理解を構築」または「理解を更新」を実行すると、ここに分かったこと・確認したいこと・次にすることが表示されます。
      </p>
    </div>
  );
}

export interface UnderstandingOverviewProps {
  understanding: CurrentUnderstanding | null | undefined;
  gaps?: GapItem[] | null;
  openQuestions?: OpenQuestion[] | null;
  // 呼び出し側 (interview.tsx) が既に算出している「次にやること」の文言を
  // そのまま渡す。このコンポーネント内でロジックを再実装しない。
  nextAction?: string | null;
}

export function UnderstandingOverview({
  understanding, gaps, openQuestions, nextAction,
}: UnderstandingOverviewProps) {
  const [detailOpen, setDetailOpen] = useState(false);

  const items = collectItems(understanding);
  const knownItems = items.filter(({ item }) => !confidenceNeedsConfirmation(item.confidence.level));
  const uncertainItems = items.filter(({ item }) => confidenceNeedsConfirmation(item.confidence.level));
  const gapItems = gaps ?? [];
  const questionItems = openQuestions ?? [];

  const hasAnyContent =
    knownItems.length > 0 || uncertainItems.length > 0 || gapItems.length > 0 || questionItems.length > 0;

  if (!hasAnyContent) {
    return <EmptyOverviewGuidance />;
  }

  const hasDetailContent = items.length > 0;

  return (
    <div className="space-y-5" data-testid="understanding-overview">
      <OverviewGroup title="分かったこと" testid="overview-group-known">
        {knownItems.length === 0 ? (
          <EmptyGroupNote text="まだ確信度の高い理解はありません。" />
        ) : (
          knownItems.map(({ key, category, item }) => (
            <OverviewItemCard
              key={key}
              id={key}
              heading={item.name}
              summary={item.summary || category}
              badgeLabel={confidenceLabel(item.confidence.level)}
              badgeVariant={confidenceBadgeVariant(item.confidence.level)}
              actionRequired={false}
              detail={understandingItemDetail(item)}
            />
          ))
        )}
      </OverviewGroup>

      <OverviewGroup title="確認したいこと" testid="overview-group-confirm">
        {uncertainItems.length === 0 && gapItems.length === 0 && questionItems.length === 0 ? (
          <EmptyGroupNote text="現時点で確認が必要な項目はありません。" />
        ) : (
          <>
            {uncertainItems.map(({ key, category, item }) => (
              <OverviewItemCard
                key={key}
                id={key}
                heading={item.name}
                summary={item.summary || category}
                badgeLabel={confidenceLabel(item.confidence.level)}
                badgeVariant={confidenceBadgeVariant(item.confidence.level)}
                actionRequired
                detail={understandingItemDetail(item)}
              />
            ))}
            {gapItems.map((gap, idx) => (
              <OverviewItemCard
                key={`gap-${idx}`}
                id={`gap-${idx}`}
                heading={gap.name}
                summary={gap.summary || gapTypeLabel(gap.gap_type)}
                badgeLabel={severityLabel(gap.severity)}
                badgeVariant={severityBadgeVariant(gap.severity)}
                actionRequired
                detail={<p>種別: {gapTypeLabel(gap.gap_type)}</p>}
              />
            ))}
            {questionItems.map((q, idx) => (
              <OverviewItemCard
                key={`question-${idx}`}
                id={`question-${idx}`}
                heading={q.question}
                badgeLabel={`優先度: ${priorityLabel(q.priority)}`}
                badgeVariant={priorityBadgeVariant(q.priority)}
                actionRequired
                detail={questionDetail(q)}
              />
            ))}
          </>
        )}
      </OverviewGroup>

      <OverviewGroup title="次にすること" testid="overview-group-next">
        <div className="rounded-md border bg-muted/40 p-3 text-sm" data-testid="overview-next-action">
          {nextAction || "特に対応は必要ありません。"}
        </div>
      </OverviewGroup>

      {hasDetailContent && (
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setDetailOpen(o => !o)}
            aria-expanded={detailOpen}
            aria-controls="understanding-overview-detail"
            data-testid="toggle-understanding-detail"
          >
            {detailOpen ? "カテゴリ別の詳細を隠す" : "カテゴリ別の詳細を見る"}
          </Button>
          {detailOpen && understanding && (
            <div
              id="understanding-overview-detail"
              className="mt-3"
              data-testid="understanding-overview-detail"
            >
              <UnderstandingCategoryDetail understanding={understanding} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
