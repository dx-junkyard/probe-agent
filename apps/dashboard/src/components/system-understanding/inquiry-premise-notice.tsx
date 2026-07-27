// Issue #322 (dashboard half of Issue #308; server side is #320/#321/#323):
// Inquiry の「前提」表示。
//
// Inquiry(#285)は確認項目に対する脇道の会話で、作成時点の前提(snapshot /
// revision / 対象レビュー項目の内容ハッシュ)がサーバーに記録されている。
// Alignment を再ビルドすると、その前提が現行のレビュー項目から失われている
// Inquiry はサーバー側の決定的な評価によって status='superseded' になる。
//
// superseded の意味は「当時の前提に対する会話は履歴として残すが、現行の判断
// には流用しない」であり、
//   - 回答が誤っていた、という意味ではない
//   - 未解決(unresolved)でも取り消し(cancelled)でもない
// この違いが画面上で読み取れるように、固定文言のバッジ + 説明文 + 理由の3点
// を必ず並べて表示する。
//
// 表示はすべてサーバーが返すフィールドからの決定的な写像だけで組み立てる:
//   - premise_evaluation (changed / removed / ambiguous) …… 失効理由
//   - premise_tracking_state === 'untrackable' …………………… 自動比較不可
//   - premise_successor_item_id ……………………………………… 後継への導線
// クライアント側で後継を推測することは絶対にしない(premise_successor_item_id
// が無ければ「特定されていません」という固定文言を出すだけ)。ローカル state
// にも依存しないので、リロードやセッション再開後も同じ表示になる。

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import type {
  InquiryPremiseTrackingState,
  InterviewInquiryOut,
} from "@/api/types";

// 失効した Inquiry に必ず付く固定バッジと、未解決/取り消しとの違いを明示す
// る固定説明文。理由ごとに文面を変えない(理由は下の固定文言で別に出す)。
export const INQUIRY_SUPERSEDED_BADGE_LABEL = "前提が変わったため再確認が必要";
export const INQUIRY_SUPERSEDED_EXPLANATION =
  "当時の前提に対する会話は履歴として残りますが、現行の判断には流用しません(未解決・取り消しとは異なります)。";

// 決定的な有限集合(Principle 6)。changed / removed / ambiguous はサーバーの
// premise_evaluation そのもの、untrackable は premise_tracking_state 由来。
export type InquiryPremiseReason = "changed" | "removed" | "ambiguous" | "untrackable";

// 理由ごとの固定日本語文言。計算・言い換え・自由文は一切しない。
export const INQUIRY_PREMISE_REASON_MESSAGES: Record<InquiryPremiseReason, string> = {
  changed: "前提となる確認項目の内容が変わりました。",
  removed: "この疑問の論点は現行のレビューから無くなりました。",
  ambiguous: "後継の確認項目を一意に特定できませんでした。",
  untrackable: "作成時の前提を自動で比較できません(旧データ、または内容のハッシュが不明です)。",
};

// 監査詳細に出す短いラベル(上の説明文の見出し相当)。
export const INQUIRY_PREMISE_REASON_LABELS: Record<InquiryPremiseReason, string> = {
  changed: "内容が変わった",
  removed: "論点が無くなった",
  ambiguous: "後継を一意に特定できない",
  untrackable: "自動比較できない",
};

// 後継が一意に定まらないときの固定文言。ここでクライアントが後継を推測する
// ことは禁止(サーバーが一意に特定できた場合だけ導線を出す)。
export const INQUIRY_SUCCESSOR_UNKNOWN_MESSAGE =
  "後継の確認項目は特定されていません。必要であれば、現在の確認項目から新しい疑問を開始してください。";

const TRACKING_STATE_LABELS: Record<InquiryPremiseTrackingState, string> = {
  not_applicable: "対象外(レビュー項目以外)",
  untrackable: "自動比較なし",
  tracked: "追跡あり",
};

function formatTimestamp(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ja-JP");
}

/**
 * サーバーのフィールドから失効理由を決定的に導く。
 *
 * premise_evaluation が changed / removed / ambiguous のいずれかならそれを、
 * そうでなく premise_tracking_state が untrackable ならそれを返す。
 * 'unchanged' は失効させないので何も返さない(null)。判定不能なときに理由を
 * 推測してはいけないので、その場合も null。
 */
export function inquiryPremiseReason(
  inquiry: Pick<InterviewInquiryOut, "premise_evaluation" | "premise_tracking_state">,
): InquiryPremiseReason | null {
  const evaluation = inquiry.premise_evaluation ?? null;
  if (evaluation === "changed" || evaluation === "removed" || evaluation === "ambiguous") {
    return evaluation;
  }
  if (inquiry.premise_tracking_state === "untrackable") return "untrackable";
  return null;
}

/** superseded は終端状態で、再開・回答・解消のいずれも提示してはいけない。 */
export function isSupersededInquiry(
  inquiry: Pick<InterviewInquiryOut, "status">,
): boolean {
  return inquiry.status === "superseded";
}

/**
 * 失効バッジ / 理由 / 後継導線 / 監査詳細。
 *
 * superseded でも untrackable でもない Inquiry には何も描画しない
 * (premise_tracking_state='not_applicable' も同じく無表示)。
 */
export function InquiryPremiseNotice({ inquiry }: { inquiry: InterviewInquiryOut }) {
  const [showAudit, setShowAudit] = useState(false);
  const superseded = isSupersededInquiry(inquiry);
  const reason = inquiryPremiseReason(inquiry);
  if (!superseded && reason !== "untrackable") return null;

  const successorItemId = inquiry.premise_successor_item_id ?? null;

  return (
    <div
      className="rounded-md border border-amber-500/60 bg-amber-500/5 p-2 text-xs space-y-1"
      data-testid={`inquiry-premise-notice-${inquiry.id}`}
    >
      {superseded && (
        <Badge variant="warning" data-testid={`inquiry-superseded-badge-${inquiry.id}`}>
          {INQUIRY_SUPERSEDED_BADGE_LABEL}
        </Badge>
      )}
      {superseded && (
        <p className="text-muted-foreground" data-testid={`inquiry-superseded-explanation-${inquiry.id}`}>
          {INQUIRY_SUPERSEDED_EXPLANATION}
        </p>
      )}
      {reason && (
        <p className="font-medium text-amber-800" data-testid={`inquiry-premise-reason-${inquiry.id}`}>
          {INQUIRY_PREMISE_REASON_MESSAGES[reason]}
        </p>
      )}
      {superseded && (
        successorItemId !== null ? (
          <a
            className="inline-block text-primary underline underline-offset-2"
            href={`#review-item-${successorItemId}`}
            data-testid={`inquiry-successor-link-${inquiry.id}`}
          >
            後継の確認項目 #{successorItemId} を開く
          </a>
        ) : (
          <p className="text-muted-foreground" data-testid={`inquiry-successor-unknown-${inquiry.id}`}>
            {INQUIRY_SUCCESSOR_UNKNOWN_MESSAGE}
          </p>
        )
      )}
      <button
        type="button"
        className="text-xs text-primary underline underline-offset-2"
        onClick={() => setShowAudit(s => !s)}
        aria-expanded={showAudit}
        data-testid={`inquiry-premise-audit-toggle-${inquiry.id}`}
      >
        {showAudit ? "前提の詳細を隠す" : "前提の詳細を見る"}
      </button>
      {showAudit && (
        <div
          className="space-y-0.5 text-[11px] text-muted-foreground"
          data-testid={`inquiry-premise-audit-${inquiry.id}`}
        >
          <p>
            <span className="font-semibold">作成時のスナップショット:</span>{" "}
            {inquiry.premise_snapshot_id != null ? `#${inquiry.premise_snapshot_id}` : "記録なし"}
          </p>
          <p>
            <span className="font-semibold">作成時のリビジョン:</span>{" "}
            {inquiry.premise_revision_id != null ? `#${inquiry.premise_revision_id}` : "記録なし"}
          </p>
          <p>
            <span className="font-semibold">失効理由:</span>{" "}
            {reason ? INQUIRY_PREMISE_REASON_LABELS[reason] : "記録なし"}
          </p>
          <p>
            <span className="font-semibold">前提の追跡状態:</span>{" "}
            {TRACKING_STATE_LABELS[inquiry.premise_tracking_state] ?? "記録なし"}
          </p>
          {inquiry.premise_captured_at != null && (
            <p>
              <span className="font-semibold">前提の記録日時:</span>{" "}
              {formatTimestamp(inquiry.premise_captured_at)}
            </p>
          )}
          {/* closed_at(解消した瞬間)と superseded_at(前提が失効した瞬間)
              は別物なので、両方あるときは両方出す。 */}
          {inquiry.closed_at != null && (
            <p data-testid={`inquiry-premise-closed-at-${inquiry.id}`}>
              <span className="font-semibold">解消日時:</span> {formatTimestamp(inquiry.closed_at)}
            </p>
          )}
          {inquiry.superseded_at != null && (
            <p data-testid={`inquiry-premise-superseded-at-${inquiry.id}`}>
              <span className="font-semibold">失効日時:</span> {formatTimestamp(inquiry.superseded_at)}
            </p>
          )}
          {inquiry.premise_tracking_version && (
            <p className="font-mono">tracking_version: {inquiry.premise_tracking_version}</p>
          )}
          {/* status_reason はサーバーの有限な reason code(技術識別子)なので
              監査行としてそのまま出す — 画面本文の説明には使わない。 */}
          {superseded && inquiry.status_reason && (
            <p className="font-mono">reason_code: {inquiry.status_reason}</p>
          )}
          {inquiry.premise_review_subject_id && (
            <p className="font-mono break-all">
              review_subject_id: {inquiry.premise_review_subject_id}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
