// Issue #356 §7: セッション情報。
//
// 右カラム下部に、参加者・最終更新・根拠数・保存状態をまとめる。既存の
// 「セッション #id」カードを置き換える形で使い、同じ対象のカードを 2 枚に
// しない (原則 P7)。

import type { ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatTimestamp } from "@/lib/utils";

export function CockpitSessionInfo({
  sessionId,
  snapshotId,
  status,
  updatedAt,
  participants,
  evidenceCounts,
  saving,
  children,
}: {
  sessionId: number;
  snapshotId: number;
  status: string;
  updatedAt: number;
  participants: string[];
  evidenceCounts: { code: number; docs: number };
  /** 進行中の保存があるか。無ければサーバー永続済み。 */
  saving: boolean;
  children?: ReactNode;
}) {
  return (
    <Card data-testid="cockpit-session-info">
      <CardHeader>
        <CardTitle className="text-sm">セッション #{sessionId}</CardTitle>
        <CardDescription>
          Snapshot {snapshotId} · {status}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <dl className="grid grid-cols-[6rem_1fr] gap-y-1 text-xs">
          <dt className="text-muted-foreground">参加者</dt>
          <dd className="break-words" data-testid="cockpit-session-participants">
            {participants.length > 0 ? participants.join(", ") : "-"}
          </dd>
          <dt className="text-muted-foreground">最終更新</dt>
          <dd data-testid="cockpit-session-updated">{formatTimestamp(updatedAt)}</dd>
          <dt className="text-muted-foreground">根拠</dt>
          <dd data-testid="cockpit-session-evidence">
            コード {evidenceCounts.code} 件 · ドキュメント {evidenceCounts.docs} 件
          </dd>
          <dt className="text-muted-foreground">保存状態</dt>
          <dd data-testid="cockpit-session-saved">{saving ? "保存中..." : "保存済み"}</dd>
        </dl>
        {children}
      </CardContent>
    </Card>
  );
}
