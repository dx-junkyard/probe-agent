// Issue #356 §6: Q&A 進捗。
//
// ドーナツチャートは補助表現で、値は必ずラベル + 数値のテキストでも読める
// (色だけに依存しない / 読み上げ可能にする)。件数は `qaProgress` が決めた
// 値で、回答済み + 確認待ち + 未回答 = 合計 が常に成り立つ。

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { CockpitQaProgress } from "./model";

const COLORS = {
  answered: "#10b981",
  awaiting: "#f59e0b",
  open: "#cbd5e1",
} as const;

export function CockpitQaProgressCard({ progress }: { progress: CockpitQaProgress }) {
  const { answered, awaiting, open, deferred, total, excluded } = progress;
  const pct = (value: number) => (total > 0 ? (value / total) * 100 : 0);
  const answeredEnd = pct(answered);
  const awaitingEnd = answeredEnd + pct(awaiting);
  const gradient =
    total > 0
      ? `conic-gradient(${COLORS.answered} 0 ${answeredEnd}%, ${COLORS.awaiting} ${answeredEnd}% ${awaitingEnd}%, ${COLORS.open} ${awaitingEnd}% 100%)`
      : undefined;

  const rows: Array<[string, number, string, string]> = [
    ["cockpit-qa-answered", answered, "回答済み", COLORS.answered],
    ["cockpit-qa-awaiting", awaiting, "確認待ち", COLORS.awaiting],
    ["cockpit-qa-open", open, "未回答", COLORS.open],
  ];

  return (
    <Card data-testid="cockpit-qa-progress">
      <CardHeader>
        <CardTitle className="text-sm">Q&A の進捗</CardTitle>
        <CardDescription>
          回答済み {answered} 件 / 確認待ち {awaiting} 件 / 未回答 {open} 件 (合計 {total} 件)
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {total === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="cockpit-qa-empty">
            この セッションにはまだ質問がありません。
          </p>
        ) : (
          <>
            <div className="flex justify-center">
              <div
                className="relative grid h-24 w-24 place-items-center rounded-full"
                style={gradient ? { background: gradient } : undefined}
                role="img"
                aria-label={`Q&A の進捗: 合計 ${total} 件のうち、回答済み ${answered} 件、確認待ち ${awaiting} 件、未回答 ${open} 件`}
                data-testid="cockpit-qa-donut"
              >
                <div className="grid h-16 w-16 place-items-center rounded-full bg-background text-center">
                  <div>
                    <span className="text-lg font-bold">{answered}</span>
                    <span className="text-xs text-muted-foreground">/{total}</span>
                    <span className="block text-[10px] text-muted-foreground">回答済み</span>
                  </div>
                </div>
              </div>
            </div>
            <dl className="space-y-1">
              {rows.map(([testId, value, label, color]) => (
                <div key={testId} className="flex items-center text-xs" data-testid={testId}>
                  <span
                    className="mr-2 h-2 w-2 rounded-full"
                    style={{ backgroundColor: color }}
                    aria-hidden="true"
                  />
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="ml-auto font-semibold">{value} 件</dd>
                </div>
              ))}
              <div className="flex items-center border-t pt-1 text-xs" data-testid="cockpit-qa-total">
                <dt className="text-muted-foreground">合計</dt>
                <dd className="ml-auto font-semibold">{total} 件</dd>
              </div>
            </dl>
            {/* 「後で回答」は未回答の内訳。解決済みに見えないよう再掲する。 */}
            {deferred > 0 && (
              <p className="text-[10px] text-muted-foreground" data-testid="cockpit-qa-deferred">
                未回答のうち {deferred} 件は「後で回答」として見送り中です。
              </p>
            )}
          </>
        )}
        {excluded > 0 && (
          <p className="text-[10px] text-muted-foreground" data-testid="cockpit-qa-excluded">
            訂正で置き換えられた質問 {excluded} 件は合計に含めていません。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
