// Issue #356 §7 / Issue #361: セッション情報。
//
// 参加者・最終更新・根拠数・保存状態をまとめる。#356 では右カラム下部の常設
// カードだったが、#361 で補助情報の折りたたみへ移した。
//
// セッション番号 / Snapshot 番号 / ステータスはここには出さない。ヘッダーの
// `cockpit-header-meta` が同じ 3 つを常時表示しており、そちらが正準である
// (同じ対象を複数箇所へ常時表示しない、原則 P7 / issue #361 の受け入れ条件
// 「セッション情報とヘッダーの重複表示が解消される」)。ヘッダーの
// 「セッション情報」ボタンがこのカードへの入口になる。

import type { ReactNode } from "react";
import { formatTimestamp } from "@/lib/utils";

export function CockpitSessionInfo({
  updatedAt,
  participants,
  evidenceCounts,
  saving,
  children,
}: {
  updatedAt: number;
  participants: string[];
  evidenceCounts: { code: number; docs: number };
  /** 進行中の保存があるか。無ければサーバー永続済み。 */
  saving: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-3 text-sm" data-testid="cockpit-session-info">
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
    </div>
  );
}
