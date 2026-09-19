// Issue #464 — Interview の前提 (premise) 表示と、前提が動いたときの明示選択。
//
// この System の正準 Understanding は 1 つで、Interview はその特定リビジョンを
// 前提に行う履歴である。前提が動いた会話をそのまま続けると、同じプロジェクト
// について画面ごとに違う認識が同時に成立してしまう ── Overview は Vision を
// 把握しているのに Interview は未把握、という報告された症状がまさにそれ。
//
// 表示はすべてサーバーが返した値の決定的な写像で、ここで判定をやり直さない:
//   - state / reason_code / message …… サーバーの first-match 判定と固定文言
//   - available_actions ……………………… 提示してよい選択肢の有限集合
//   - continuable …………………………… 会話を続けられるか
// クライアントが「たぶん大丈夫」と補完すると、API が 409 を返す状態で画面だけ
// が続行可能に見える。それは #349 が状態導出をサーバーへ寄せた理由と同じ。
//
// `active` は「決定がまだ無い」であって決定ではないので、state が current の
// ときは何も出さない(正常状態にバッジを出すと、本当に読むべきときに読まれ
// なくなる)。

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { InterviewPremiseOut } from "@/api/types";

// 提示してよい選択肢の固定ラベルと説明。サーバーの available_actions に無い
// ものは決して描かない。
const ACTION_LABELS: Record<string, { label: string; description: string }> = {
  continue: {
    label: "このまま続ける",
    description: "現在の正準 Understanding と一致しています。",
  },
  start_new_session: {
    label: "新しい Interview を開始",
    description: "現在の正準 Understanding を前提に、新しい会話を始めます。",
  },
  rebase: {
    label: "現在の前提へ rebase",
    description:
      "この検討を、現在の正準 Understanding を前提とする新しいセッションへ引き継ぎます。元の会話と前提はそのまま残ります。",
  },
  branch: {
    label: "古い前提のまま維持 (branch)",
    description:
      "古い前提の検討として、この会話を明示的に続けます。判定は stale のままで、この会話の候補を正準へ昇格することはできません。",
  },
  adopt_current_premise: {
    label: "現在の前提を採用",
    description:
      "この会話が何を前提に始まったか記録がありません。現在の正準 Understanding を前提として明示的に結び直します。",
  },
  open_successor_session: {
    label: "引き継ぎ先のセッションを開く",
    description: "この会話は rebase 済みで、続きは後続のセッションにあります。",
  },
};

const STATE_TONE: Record<InterviewPremiseOut["state"], string> = {
  current: "border-emerald-300 bg-emerald-50",
  stale: "border-amber-300 bg-amber-50",
  missing: "border-amber-300 bg-amber-50",
  invalid: "border-slate-300 bg-slate-50",
};

export interface PremiseNoticeProps {
  premise: InterviewPremiseOut;
  pending?: boolean;
  onRebase: () => void;
  onBranch: () => void;
  onAdoptCurrent: () => void;
  onStartNewSession: () => void;
  onOpenSession: (sessionId: number) => void;
}

export function PremiseNotice({
  premise,
  pending = false,
  onRebase,
  onBranch,
  onAdoptCurrent,
  onStartNewSession,
  onOpenSession,
}: PremiseNoticeProps) {
  const [detailOpen, setDetailOpen] = useState(false);

  // 正常な前提には何も出さない。ただし branch 済みは「古い前提のまま進んで
  // いる」という、読めていないと誤解を生む状態なので出し続ける。
  if (premise.state === "current" && premise.disposition === "active") return null;

  const actions = premise.available_actions.filter(a => a !== "continue");

  function runAction(action: string) {
    if (action === "rebase") return onRebase();
    if (action === "branch") return onBranch();
    if (action === "adopt_current_premise") return onAdoptCurrent();
    if (action === "start_new_session") return onStartNewSession();
    if (action === "open_successor_session" && premise.successor_session_id) {
      return onOpenSession(premise.successor_session_id);
    }
  }

  return (
    <Card
      className={STATE_TONE[premise.state]}
      data-testid="interview-premise-notice"
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Badge variant="outline">{premise.label}</Badge>
          {premise.disposition !== "active" && (
            <Badge variant="outline">{premise.disposition_label}</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{premise.message}</p>

        {actions.length > 0 && (
          <div className="space-y-2">
            {actions.map(action => {
              const entry = ACTION_LABELS[action];
              if (!entry) return null;
              return (
                <div key={action} className="flex items-start gap-3">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={pending}
                    onClick={() => runAction(action)}
                  >
                    {entry.label}
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    {entry.description}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        <button
          type="button"
          className="text-xs underline text-muted-foreground"
          onClick={() => setDetailOpen(open => !open)}
        >
          {detailOpen ? "詳細を隠す" : "前提の詳細を表示"}
        </button>
        {detailOpen && (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <dt>開始時の Understanding</dt>
            <dd>{premise.base_revision_id ?? "記録なし"}</dd>
            <dt>現在の正準 head</dt>
            <dd>
              {premise.head_revision_id ?? "未設定"}
              {premise.head_version != null ? ` (v${premise.head_version})` : ""}
            </dd>
            <dt>判定理由</dt>
            <dd>{premise.reason_code}</dd>
            <dt>昇格できる候補</dt>
            <dd>{premise.promotable_revision_id ?? "なし"}</dd>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

// --- 候補の昇格 --------------------------------------------------------------
//
// 「理解の確認」(understanding_confirmed_at) と「正準 head の昇格」は別の判断
// である。前者はこのセッションの理解で先へ進んでよいという判断、後者はこの
// System が今後この理解を正本として扱うという判断で、後者だけが Overview の
// 表示と、以後に始まる Interview の前提を動かす。
//
// 昇格は compare-and-swap で、開発者が見ていた head を expected として送る。
// head が動いていれば 409 で拒否される ── その確認は別の内容についてのもの
// だったからで、マージも last-write-wins もしない (#464 非目標 3)。

export interface CanonicalPromotePanelProps {
  premise: InterviewPremiseOut;
  headRevisionId: number | null;
  headVersion: number | null;
  pending?: boolean;
  onPromote: (input: {
    revision_id: number;
    expected_head_revision_id: number | null;
    expected_head_version: number | null;
  }) => void;
}

export function CanonicalPromotePanel({
  premise,
  headRevisionId,
  headVersion,
  pending = false,
  onPromote,
}: CanonicalPromotePanelProps) {
  const revisionId = premise.promotable_revision_id;
  // 昇格できるのは前提が current のセッションの候補だけ。branch した会話の
  // 候補にボタンを出すと、押せるのに必ず 409 になる操作を見せることになる。
  if (revisionId == null || premise.state !== "current") return null;
  if (headRevisionId === revisionId) return null;

  return (
    <Card data-testid="canonical-promote-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">この理解を System の正準にする</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-muted-foreground text-xs">
          昇格すると、Overview と、これから始まる Interview の前提がこの内容に
          なります。現在の正準:{" "}
          {headRevisionId == null
            ? "未設定"
            : `#${headRevisionId} (v${headVersion ?? "?"})`}
          。
        </p>
        <Button
          size="sm"
          disabled={pending}
          onClick={() =>
            onPromote({
              revision_id: revisionId,
              expected_head_revision_id: headRevisionId,
              expected_head_version: headVersion,
            })
          }
        >
          正準として確定する
        </Button>
      </CardContent>
    </Card>
  );
}
