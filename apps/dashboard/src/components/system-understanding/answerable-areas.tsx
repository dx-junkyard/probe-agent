// Issue #291: 回答可能領域(answerable knowledge areas)。
//
// 開発者が「今回自分が答えられる領域」を明示的に選ぶ(ロールからの推論は
// しない、Principle 6)。未選択(空配列)は「フィルタなし」であって「全領域
// を選択した」ではない — この違いをラベル文言でも明示する。
//
// isOutOfArea はサーバー側 `_is_out_of_area`(routes/interview.py)と同じ
// 決定的ルールをクライアント側の表示グルーピングにも使う: 空選択なら常に
// false、質問の knowledge_area が null なら常に false(未分類の質問は絶対
// に非表示にしない)。

import { toast } from "sonner";
import { useUpdateAnswerableAreas } from "@/api/hooks";
import type { KnowledgeArea } from "@/api/types";

export const KNOWLEDGE_AREAS: KnowledgeArea[] = [
  "product_intent", "domain_rule", "operations", "implementation", "security",
];

export const KNOWLEDGE_AREA_LABELS: Record<KnowledgeArea, string> = {
  product_intent: "事業・目的",
  domain_rule: "業務ルール",
  operations: "運用",
  implementation: "実装",
  security: "セキュリティ",
};

export function knowledgeAreaLabel(area: string | null | undefined): string {
  if (!area) return "未分類";
  return KNOWLEDGE_AREA_LABELS[area as KnowledgeArea] ?? area;
}

export function isOutOfArea(
  knowledgeArea: KnowledgeArea | null | undefined,
  answerableAreas: KnowledgeArea[] | null | undefined,
): boolean {
  if (!answerableAreas || answerableAreas.length === 0) return false;
  if (!knowledgeArea) return false;
  return !answerableAreas.includes(knowledgeArea);
}

export function AnswerableAreasControl({
  sessionId, answerableAreas,
}: {
  sessionId: number;
  // Optional/possibly-undefined defensively: sessions from a server
  // predating Issue #291 (or a stale cached response) may omit this field.
  answerableAreas: KnowledgeArea[] | null | undefined;
}) {
  const update = useUpdateAnswerableAreas(sessionId);
  const areas = answerableAreas ?? [];

  const toggle = (area: KnowledgeArea) => {
    const next = areas.includes(area)
      ? areas.filter(a => a !== area)
      : [...areas, area];
    update.mutate(next, { onError: e => toast.error(String(e)) });
  };

  return (
    <div data-testid="answerable-areas-control" className="space-y-1">
      <p className="text-xs text-muted-foreground">
        今回回答できる領域(未選択の場合はすべての質問を表示します)
      </p>
      <div className="flex flex-wrap gap-1">
        {KNOWLEDGE_AREAS.map(area => {
          const selected = areas.includes(area);
          return (
            <button
              key={area}
              type="button"
              onClick={() => toggle(area)}
              disabled={update.isPending}
              aria-pressed={selected}
              data-testid={`answerable-area-chip-${area}`}
              className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                selected
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-input bg-background text-muted-foreground hover:bg-muted"
              }`}
            >
              {KNOWLEDGE_AREA_LABELS[area]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
