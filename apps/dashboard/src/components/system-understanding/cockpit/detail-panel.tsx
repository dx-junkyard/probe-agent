// Issue #356 §4: 選択項目の詳細・修正ペイン。
//
// 「今どうなっているか」「なぜ要確認・未設定なのか」「どう直すか」を 1 枚に
// まとめる。修正手段は既存 Interview 画面のパネルへ移動するだけで、回答・
// 編集・根拠表示の処理をここで重複実装しない。

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

export function CockpitDetailPanel({
  category,
  state,
  onAction,
}: {
  category: CockpitCategoryView;
  /** サーバーが決めたワークフロー状態 (Issue #349)。可否判定に使うだけ。 */
  state: InterviewWorkflowState | null;
  onAction: (targetTestId: string) => void;
}) {
  const actions = categoryActions(category, state);
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

        {(category.gaps.length > 0 || category.questions.length > 0) && (
          <section className="space-y-1" data-testid="cockpit-detail-reasons">
            <h3 className="text-xs font-semibold text-muted-foreground">
              確認が必要な理由
            </h3>
            <ul className="space-y-1">
              {category.gaps.map((gap, i) => (
                <li key={`gap-${i}`} className="text-xs break-words" data-testid="cockpit-detail-gap">
                  <span className="font-medium">{gap.name}</span>
                  {gap.summary ? ` — ${gap.summary}` : ""}
                  <span className="text-muted-foreground"> ({gap.gap_type} / {gap.severity})</span>
                </li>
              ))}
              {category.questions.map(question => (
                <li
                  key={question.id}
                  className="text-xs break-words"
                  data-testid="cockpit-detail-question"
                >
                  {question.question}
                </li>
              ))}
            </ul>
          </section>
        )}

        {(category.evidence.length > 0 || category.relatedDocs.length > 0) && (
          <section className="space-y-1" data-testid="cockpit-detail-evidence">
            <h3 className="text-xs font-semibold text-muted-foreground">根拠</h3>
            <ul className="space-y-0.5">
              {category.evidence.slice(0, 6).map((e, i) => (
                <li key={`ev-${i}`} className="font-mono text-[10px] text-muted-foreground break-all">
                  {e.path}
                  {e.start_line > 0 ? `:${e.start_line}-${e.end_line}` : ""}
                </li>
              ))}
              {category.relatedDocs.slice(0, 4).map((doc, i) => (
                <li key={`doc-${i}`} className="font-mono text-[10px] text-muted-foreground break-all">
                  {doc}
                </li>
              ))}
            </ul>
            {category.evidence.length > 6 && (
              <p className="text-[10px] text-muted-foreground">
                ほか {category.evidence.length - 6} 件の根拠があります。
              </p>
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
                onClick={() => action.targetTestId && onAction(action.targetTestId)}
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
