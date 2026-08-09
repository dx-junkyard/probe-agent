// Issue #356 §4 / Issue #363: 選択項目の詳細・修正ペイン。
//
// 「今どうなっているか」「なぜ要確認・未設定なのか」「どう直すか」を 1 枚に
// まとめる。修正手段は既存 Interview 画面のパネルへ移動するだけで、回答・
// 編集・根拠表示の処理をここで重複実装しない。
//
// Issue #363: マップのカードから外した対応ヒント (hint) をここで受け取り、
// 「今の状況」として先頭に出す。理由と根拠は初期表示を 3 件までにし、残りは
// 展開式にする -- ペインが縦に伸びると、選択したカテゴリの修正手段が画面外に
// 出てしまう。件数はモデルの値をそのまま数えるだけで、ここで状態や可否を
// 判定し直すことはしない (状態判定は `model.ts` が持つ)。

import { useState } from "react";
import { ChevronRight, FileText, MessageSquareText, Pencil } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { InterviewWorkflowState } from "@/api/types";
import {
  categoryActions,
  categoryTitle,
  type CockpitActionKind,
  type CockpitCategoryView,
} from "./model";
import { CategoryStatusBadge } from "./understanding-map";

const ACTION_ICON: Record<CockpitActionKind, typeof MessageSquareText> = {
  answer_question: MessageSquareText,
  direct_edit: Pencil,
  review_evidence: FileText,
};

/** 初期表示する件数 (理由・根拠に共通)。残りは展開式で開く。 */
const INITIAL_VISIBLE = 3;

/**
 * 展開トグル。押してもボタン自身は消えないので、フォーカスはトグルに残る
 * (開いた瞬間にフォーカスを失うと、キーボードで続きを読めない)。
 */
function DisclosureToggle({
  expanded,
  expandLabel,
  onToggle,
  testId,
}: {
  expanded: boolean;
  expandLabel: string;
  onToggle: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className="text-[11px] font-medium text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      data-testid={testId}
    >
      {expanded ? "表示を戻す" : expandLabel}
    </button>
  );
}

export function CockpitDetailPanel({
  category,
  state,
  onAction,
}: {
  category: CockpitCategoryView;
  /** サーバーが決めたワークフロー状態 (Issue #349)。可否判定に使うだけ。 */
  state: InterviewWorkflowState | null;
  /** 移動先候補 (優先度順)。呼び出し側が実際に描かれているものを探す。 */
  onAction: (targetTestIds: string[]) => void;
}) {
  const [reasonsExpanded, setReasonsExpanded] = useState(false);
  const [evidenceExpanded, setEvidenceExpanded] = useState(false);
  const actions = categoryActions(category, state);

  // 理由 = gap → 質問 の順 (これまでと同じ並び)。件数はモデルの配列長そのもの。
  const reasonCount = category.gaps.length + category.questions.length;
  const visibleGaps = reasonsExpanded
    ? category.gaps
    : category.gaps.slice(0, INITIAL_VISIBLE);
  const visibleQuestions = reasonsExpanded
    ? category.questions
    : category.questions.slice(0, Math.max(0, INITIAL_VISIBLE - category.gaps.length));

  // 根拠 = コードの根拠 → 関連ドキュメント の順。
  const evidenceCount = category.evidence.length + category.relatedDocs.length;
  const visibleEvidence = evidenceExpanded
    ? category.evidence
    : category.evidence.slice(0, INITIAL_VISIBLE);
  const visibleDocs = evidenceExpanded
    ? category.relatedDocs
    : category.relatedDocs.slice(0, Math.max(0, INITIAL_VISIBLE - category.evidence.length));

  return (
    <Card data-testid="cockpit-detail-panel" data-category={category.key}>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm">{category.title}</CardTitle>
            <CardDescription>{category.caption}</CardDescription>
          </div>
          <CategoryStatusBadge status={category.status} label={category.statusLabel} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {/* マップのカードから移した対応ヒント (Issue #363)。 */}
        {category.hint && (
          <section className="space-y-1" data-testid="cockpit-detail-hint">
            <h3 className="text-xs font-semibold text-muted-foreground">今の状況</h3>
            <p className="text-xs break-words">{category.hint}</p>
          </section>
        )}

        <section className="space-y-1" data-testid="cockpit-detail-content">
          <h3 className="text-xs font-semibold text-muted-foreground">現在の理解</h3>
          {category.items.length === 0 ? (
            <p className="text-xs text-muted-foreground">まだ内容がありません。</p>
          ) : (
            <ul className="space-y-1">
              {category.items.map((item, i) => (
                <li key={`${item.name}-${i}`} className="rounded-md border p-2 text-xs">
                  <span className="font-medium break-words">{item.name}</span>
                  {item.summary && (
                    <span className="mt-0.5 block text-muted-foreground break-words">{item.summary}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {reasonCount > 0 && (
          <section className="space-y-1" data-testid="cockpit-detail-reasons">
            <h3 className="text-xs font-semibold text-muted-foreground">
              確認が必要な理由
            </h3>
            <ul className="space-y-1">
              {visibleGaps.map((gap, i) => (
                <li key={`gap-${i}`} className="text-xs break-words" data-testid="cockpit-detail-gap">
                  <span className="font-medium">{gap.name}</span>
                  {gap.summary ? ` — ${gap.summary}` : ""}
                  <span className="text-muted-foreground"> ({gap.gap_type} / {gap.severity})</span>
                </li>
              ))}
              {visibleQuestions.map(question => (
                <li
                  key={question.id}
                  className="text-xs break-words"
                  data-testid="cockpit-detail-question"
                >
                  {question.question}
                </li>
              ))}
            </ul>
            {reasonCount > INITIAL_VISIBLE && (
              <DisclosureToggle
                expanded={reasonsExpanded}
                expandLabel={`残り ${reasonCount - INITIAL_VISIBLE} 件の理由を表示`}
                onToggle={() => setReasonsExpanded(value => !value)}
                testId="cockpit-detail-reasons-toggle"
              />
            )}
          </section>
        )}

        {evidenceCount > 0 && (
          <section className="space-y-1" data-testid="cockpit-detail-evidence">
            <h3 className="text-xs font-semibold text-muted-foreground">根拠</h3>
            <ul className="space-y-0.5">
              {visibleEvidence.map((e, i) => (
                <li key={`ev-${i}`} className="font-mono text-[10px] text-muted-foreground break-all">
                  {e.path}
                  {e.start_line > 0 ? `:${e.start_line}-${e.end_line}` : ""}
                </li>
              ))}
              {visibleDocs.map((doc, i) => (
                <li key={`doc-${i}`} className="font-mono text-[10px] text-muted-foreground break-all">
                  {doc}
                </li>
              ))}
            </ul>
            {evidenceCount > INITIAL_VISIBLE && (
              <DisclosureToggle
                expanded={evidenceExpanded}
                expandLabel={`根拠をすべて表示 (${evidenceCount} 件)`}
                onToggle={() => setEvidenceExpanded(value => !value)}
                testId="cockpit-detail-evidence-toggle"
              />
            )}
          </section>
        )}

        <section className="space-y-2" data-testid="cockpit-detail-actions">
          <h3 className="text-xs font-semibold text-muted-foreground">修正するには</h3>
          {actions.map(action => {
            const Icon = ACTION_ICON[action.kind];
            const disabled = action.disabledReason != null;
            return (
              <button
                key={action.kind}
                type="button"
                disabled={disabled}
                onClick={() => action.targetTestIds.length > 0 && onAction(action.targetTestIds)}
                className="flex w-full items-start gap-2 rounded-md border p-2 text-left transition-colors hover:bg-accent/50 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                data-testid={`cockpit-action-${action.kind}`}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-semibold">{action.title}</span>
                  <span className="mt-0.5 block text-[11px] text-muted-foreground break-words">
                    {action.disabledReason ?? action.description}
                  </span>
                </span>
                {!disabled && <ChevronRight className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
              </button>
            );
          })}
        </section>

        {category.downstream.length > 0 && (
          <section className="space-y-1" data-testid="cockpit-detail-downstream">
            <h3 className="text-xs font-semibold text-muted-foreground">
              この項目を変えると再確認が必要になる項目
            </h3>
            <p className="text-xs text-muted-foreground">
              {category.downstream.map(categoryTitle).join(" / ")}
            </p>
          </section>
        )}
      </CardContent>
    </Card>
  );
}
