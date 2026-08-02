// Issue #351 / #352 / #353 / #354: Understanding Brief と Decision Readiness。
//
// インタビュー画面を開いた開発者が 5 秒で答えられるようにする 3 点:
//   1. AI はこのシステムを何のためのものと理解しているか (Vision / Purpose /
//      主要機能)
//   2. その理解のどこが確実で、どこが未確認か (確認状態 × 出所)
//   3. 現在の理解のまま進めてよいか、その理由は何か (Decision Readiness)
//
// 判定はすべてサーバー (`GET /interview/understanding-brief`) が永続事実から
// 決定する。このファイルは決まったものを描くだけで、確認状態・出所・進行可否
// をクライアントで再導出しない (Issue #349 が確立した原則 P9 と同じ理由:
// クライアント限定の値はリロードで消える)。
//
// 表示密度だけはワークフロー状態から選ぶ。これは「どの状態か」の判定ではなく
// 「決まった状態をどれだけ広げて出すか」の固定表であり、状態自体は常にサーバー
// の `workflow.state` である (§3.3 の状態 × 役割マトリクスと同じ扱い)。
//
// 表示上の原則 (#353):
//   - 全体を合成した信頼度パーセントを出さない
//   - 色だけで状態を伝えない (バッジは必ずテキストを伴う)
//   - 「警告 N 件」だけで進行可否を表現しない (理由は対象を伴う)
//   - Readiness とワークフロー状態を混同しない (別の見出し・別の語彙)

import { useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, HelpCircle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  InterviewWorkflowState,
  UnderstandingBriefClaimOut,
  UnderstandingBriefOut,
  UnderstandingConfirmationState,
  UnderstandingProvenanceKind,
  UnderstandingReadinessSeverity,
  UnderstandingReadinessState,
} from "@/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

/** 表示密度。状態そのものではなく、状態ごとの展開量 (#354 §状態ごとの提示)。 */
export type BriefDisplay = "empty" | "building" | "full" | "compact";

export const BRIEF_DISPLAY_BY_STATE: Record<InterviewWorkflowState, BriefDisplay> = {
  // W0: まだ理解が存在しない。架空の Vision / Purpose を事実として出さない。
  "W0-A": "empty",
  "W0-B": "empty",
  // W1: 骨格と「何を構築しているか」だけ。編集・確定できるように見せない。
  W1: "building",
  // W2: 判断そのものが主作業。全面表示。
  W2: "full",
  // W3〜W7: 主作業を妨げないコンパクト版。展開すると W2 と同じ Brief に届く。
  W3: "compact",
  W4: "compact",
  W5: "compact",
  W6: "compact",
  W7: "compact",
};

// ── サーバー値 → 表示属性の単一マッピング ────────────────────────────
// ラベル文言はサーバーが返す (`confirmation_label` / `provenance_label` /
// `readiness_label`)。ここに持つのはバッジの見た目と、サーバーが古い場合の
// 最終手段のフォールバック文言だけで、フォールバックも日本語にする。

const CONFIRMATION_VARIANT: Record<UnderstandingConfirmationState, BadgeVariant> = {
  confirmed: "success",
  ai_hypothesis: "outline",
  conflicting: "destructive",
  unknown: "outline",
  recheck_required: "warning",
};

const CONFIRMATION_FALLBACK: Record<UnderstandingConfirmationState, string> = {
  confirmed: "確認済み",
  ai_hypothesis: "AI 仮説・未確認",
  conflicting: "矛盾あり",
  unknown: "不明・情報不足",
  recheck_required: "変更後の再確認待ち",
};

const PROVENANCE_FALLBACK: Record<UnderstandingProvenanceKind, string> = {
  developer_intent: "開発者が明示した意図",
  implementation_fact: "コード・ドキュメントの実装事実",
  runtime_observation: "Runtime の観測",
  ai_hypothesis: "AI の解釈・仮説",
};

/** 確認済み以外は「開発者が見るべき対象」として扱う (安全側)。 */
function needsAttention(confirmation: UnderstandingConfirmationState): boolean {
  return confirmation !== "confirmed";
}

const READINESS_VARIANT: Record<UnderstandingReadinessState, BadgeVariant> = {
  not_built: "outline",
  building: "secondary",
  needs_confirmation: "warning",
  ready: "success",
  recheck_required: "warning",
  blocked: "destructive",
};

const SEVERITY_LABEL: Record<UnderstandingReadinessSeverity, string> = {
  blocking: "進行不可の理由",
  attention: "確認が必要",
  informational: "参考情報",
};

const SEVERITY_ORDER: UnderstandingReadinessSeverity[] = [
  "blocking",
  "attention",
  "informational",
];

const CLAIM_KIND_HEADING: Record<string, string> = {
  vision: "Vision",
  system_purpose: "System Purpose",
  core_capability: "主要機能",
};

const DETAIL_SECTION_LABELS: Record<string, string> = {
  capability_elements: "機能要素",
  supporting_elements: "支援要素",
  api_boundaries: "API 境界",
  probe_flow_candidates: "プローブ対象フロー候補",
};

// ── 部品 ──────────────────────────────────────────────────────────────

function Disclosure({
  id, label, openLabel, children,
}: {
  id: string;
  label: string;
  openLabel: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        className="text-xs text-primary underline underline-offset-2"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls={id}
        data-testid={`brief-toggle-${id}`}
      >
        {open ? openLabel : label}
      </button>
      {open && (
        <div id={id} className="mt-2" data-testid={`brief-detail-${id}`}>
          {children}
        </div>
      )}
    </div>
  );
}

/**
 * 1 つの主張 (Vision / Purpose / 主要機能)。
 *
 * 確認状態と出所を別々のバッジで並べるのが要点 — 「確認済み」と「開発者の
 * 意図」を 1 つの表現に畳むと、AI の推定を人が承認したのか、人が書いたのかが
 * 区別できなくなる (#351/#352)。
 */
export function BriefClaimCard({
  claim, testid,
}: {
  claim: UnderstandingBriefClaimOut;
  testid: string;
}) {
  const attention = needsAttention(claim.confirmation);
  const hasDetail =
    !!claim.reason
    || !!claim.contribution
    || claim.evidence.length > 0
    || claim.related_docs.length > 0
    || claim.related_apis.length > 0;
  return (
    <div
      className="rounded-md border p-3 space-y-2"
      data-testid={testid}
      data-confirmation={claim.confirmation}
      data-provenance={claim.provenance}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium break-words">{claim.name}</p>
          {claim.summary && (
            <p className="text-xs text-muted-foreground mt-0.5 break-words">{claim.summary}</p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge variant={CONFIRMATION_VARIANT[claim.confirmation] ?? "outline"} className="gap-1">
            {attention ? (
              <AlertCircle className="h-3 w-3" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
            )}
            {/* 色だけに頼らない: 読み上げにも区分を明示する。 */}
            <span className="sr-only">確認状態: </span>
            {claim.confirmation_label || CONFIRMATION_FALLBACK[claim.confirmation]}
          </Badge>
          <Badge variant="outline" className="font-normal">
            <span className="sr-only">出所: </span>
            {claim.provenance_label || PROVENANCE_FALLBACK[claim.provenance]}
          </Badge>
          {/* モック LLM 由来を隠さずに出す (CLAUDE.md: mock を分析済みデータ
              として黙って見せない)。 */}
          {claim.is_mock && (
            <Badge variant="warning" className="font-normal" data-testid="brief-claim-mock">
              モック
            </Badge>
          )}
        </div>
      </div>
      {hasDetail && (
        <Disclosure
          id={testid}
          label="根拠を見る"
          openLabel="根拠を隠す"
        >
          <div className="space-y-1 text-[11px] text-muted-foreground">
            {claim.contribution && (
              <p><span className="font-semibold">目的への貢献: </span>{claim.contribution}</p>
            )}
            {claim.reason && (
              <p><span className="font-semibold">確認状態の理由: </span>{claim.reason}</p>
            )}
            {claim.evidence.length > 0 && (
              <div className="space-y-0.5">
                <p className="font-semibold">根拠コード:</p>
                {claim.evidence.map((e, i) => (
                  <p key={i} className="font-mono break-all">
                    {e.path}:{e.start_line}-{e.end_line}
                    {e.summary ? ` — ${e.summary}` : ""}
                  </p>
                ))}
              </div>
            )}
            {claim.related_docs.length > 0 && <p>関連ドキュメント: {claim.related_docs.join("、")}</p>}
            {claim.related_apis.length > 0 && <p>関連 API: {claim.related_apis.join("、")}</p>}
          </div>
        </Disclosure>
      )}
    </div>
  );
}

/**
 * Decision Readiness — 結論と理由を同じ表示領域に置く (#353)。
 * ワークフロー状態とは別の語彙・別の見出しで示し、混同させない。
 */
export function ReadinessSummary({ brief }: { brief: UnderstandingBriefOut }) {
  const reasons = [...brief.readiness_reasons].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
  );
  return (
    <div
      className="rounded-md border p-3 space-y-2"
      data-testid="readiness-summary"
      data-readiness-state={brief.readiness_state}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase font-semibold text-muted-foreground">
          この理解で進めるか
        </span>
        <Badge variant={READINESS_VARIANT[brief.readiness_state] ?? "outline"}>
          {brief.readiness_label}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground">{brief.readiness_description}</p>
      {reasons.length > 0 && (
        <ul className="space-y-1" data-testid="readiness-reasons">
          {reasons.map((reason, i) => (
            <li
              key={`${reason.code}-${i}`}
              className="text-xs flex items-start gap-1.5"
              data-testid={`readiness-reason-${reason.code}`}
              data-severity={reason.severity}
            >
              <span className="sr-only">{SEVERITY_LABEL[reason.severity]}: </span>
              <Badge
                variant={
                  reason.severity === "blocking"
                    ? "destructive"
                    : reason.severity === "attention"
                      ? "warning"
                      : "outline"
                }
                className="shrink-0 font-normal"
              >
                {SEVERITY_LABEL[reason.severity]}
              </Badge>
              <span className="break-words">{reason.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * 前回確認時からの変更 (#353)。確認済みの内容を黙って差し替えない、の表示側。
 * 全差分・監査ログはここには出さない (既存の理解差分パネルが担う)。
 */
export function ChangesSinceConfirmation({ brief }: { brief: UnderstandingBriefOut }) {
  if (brief.changes_since_confirmation.length === 0) return null;
  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs space-y-1 dark:border-amber-800 dark:bg-amber-950/20"
      data-testid="brief-changes-since-confirmation"
    >
      <p className="font-medium">
        前回の確認後に変わった内容 ({brief.changes_since_confirmation.length} 件)
      </p>
      <ul className="space-y-0.5">
        {brief.changes_since_confirmation.map((change, i) => (
          <li key={`${change.section}-${change.name}-${i}`} data-testid="brief-change-row">
            {change.section_label}「{change.name}」: {change.detail}
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground">
        変更された内容は、前回の確定をそのまま再利用せずに確認してください。
      </p>
    </div>
  );
}

function VisionBlock({ brief }: { brief: UnderstandingBriefOut }) {
  return (
    <section aria-labelledby="brief-vision-heading" className="space-y-2">
      <h4 id="brief-vision-heading" className="text-xs font-semibold uppercase text-muted-foreground">
        {CLAIM_KIND_HEADING.vision} — 誰の状態を、どのように変えたいか
      </h4>
      {brief.vision ? (
        <BriefClaimCard claim={brief.vision} testid="brief-claim-vision" />
      ) : (
        <div className="rounded-md border border-dashed p-3 text-xs space-y-1" data-testid="brief-vision-missing">
          <p className="flex items-center gap-1.5">
            <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
            Vision は推定できていません。コードだけでは確定できない場合があります。
          </p>
          {brief.vision_missing_information.length > 0 && (
            <p className="text-muted-foreground">
              不足している情報: {brief.vision_missing_information.join("、")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function PurposeBlock({ brief }: { brief: UnderstandingBriefOut }) {
  return (
    <section aria-labelledby="brief-purpose-heading" className="space-y-2">
      <h4 id="brief-purpose-heading" className="text-xs font-semibold uppercase text-muted-foreground">
        {CLAIM_KIND_HEADING.system_purpose} — Vision に対してこのシステムが担う役割
      </h4>
      {brief.system_purpose.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="brief-purpose-missing">
          System Purpose を抽出できていません。
        </p>
      ) : (
        brief.system_purpose.map((claim, i) => (
          <BriefClaimCard key={i} claim={claim} testid={`brief-claim-purpose-${i}`} />
        ))
      )}
    </section>
  );
}

function CapabilitiesBlock({ brief }: { brief: UnderstandingBriefOut }) {
  const initial = brief.core_capabilities.slice(0, brief.core_capability_initial_count);
  const rest = brief.core_capabilities.slice(brief.core_capability_initial_count);
  return (
    <section aria-labelledby="brief-capabilities-heading" className="space-y-2">
      <h4
        id="brief-capabilities-heading"
        className="text-xs font-semibold uppercase text-muted-foreground"
      >
        {CLAIM_KIND_HEADING.core_capability} — Purpose を支える主な機能
      </h4>
      {brief.core_capabilities.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="brief-capabilities-missing">
          主要機能を抽出できていません。
        </p>
      ) : (
        <>
          {initial.map((claim, i) => (
            <BriefClaimCard key={i} claim={claim} testid={`brief-claim-capability-${i}`} />
          ))}
          {rest.length > 0 && (
            <Disclosure
              id="brief-more-capabilities"
              label={`残りの主要機能 ${rest.length} 件を見る`}
              openLabel="残りの主要機能を隠す"
            >
              <div className="space-y-2">
                {rest.map((claim, i) => (
                  <BriefClaimCard
                    key={i}
                    claim={claim}
                    testid={`brief-claim-capability-${brief.core_capability_initial_count + i}`}
                  />
                ))}
              </div>
            </Disclosure>
          )}
        </>
      )}
    </section>
  );
}

function KeyUnconfirmedBlock({ brief }: { brief: UnderstandingBriefOut }) {
  if (brief.key_unconfirmed.length === 0) return null;
  return (
    <section aria-labelledby="brief-unconfirmed-heading" className="space-y-2">
      <h4
        id="brief-unconfirmed-heading"
        className="text-xs font-semibold uppercase text-muted-foreground"
      >
        重要な未確認事項
      </h4>
      <ul className="space-y-1" data-testid="brief-key-unconfirmed">
        {brief.key_unconfirmed.map((claim, i) => (
          <li key={i} className="text-xs flex items-start gap-1.5" data-testid="brief-key-unconfirmed-row">
            <Badge
              variant={CONFIRMATION_VARIANT[claim.confirmation] ?? "outline"}
              className="shrink-0 font-normal"
            >
              {claim.confirmation_label || CONFIRMATION_FALLBACK[claim.confirmation]}
            </Badge>
            <span className="break-words">
              {CLAIM_KIND_HEADING[claim.kind] ?? claim.kind}「{claim.name}」
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 必要時に開く詳細の入口。初期表示を占有させない (#351 の段階的開示)。 */
function DetailEntry({
  brief, fullTree,
}: {
  brief: UnderstandingBriefOut;
  fullTree?: ReactNode;
}) {
  const counts = Object.entries(brief.detail_counts).filter(([, n]) => n > 0);
  if (counts.length === 0 && !fullTree) return null;
  return (
    <Disclosure
      id="brief-details"
      label="API 境界・構成要素・全文の理解ツリーを見る"
      openLabel="詳細を隠す"
    >
      <div className="space-y-3">
        {counts.length > 0 && (
          <p className="text-xs text-muted-foreground" data-testid="brief-detail-counts">
            {counts
              .map(([section, n]) => `${DETAIL_SECTION_LABELS[section] ?? section}: ${n} 件`)
              .join(" · ")}
          </p>
        )}
        {fullTree}
      </div>
    </Disclosure>
  );
}

export interface UnderstandingBriefPanelProps {
  brief: UnderstandingBriefOut | null | undefined;
  /** サーバーが決定したワークフロー状態。表示密度の選択にのみ使う。 */
  state: InterviewWorkflowState | null;
  /**
   * `W2` の主操作 (「この理解で進む」)。判断対象と同じ作業面に置くため、
   * 呼び出し側のボタンをこのカードの中で描く (#349 の原則 P2)。
   */
  primaryAction?: ReactNode;
  /** R4: 全文の理解ツリー。折りたたみの中だけに置く。 */
  fullTree?: ReactNode;
}

/**
 * 全ワークフロー状態で同じ概念・同じ位置に出る「現在のシステム理解」。
 * W2 では主作業として全面表示、それ以外はコンパクト表示で、展開すると
 * 同じ Brief に到達する (#354)。
 */
export function UnderstandingBriefPanel({
  brief, state, primaryAction, fullTree,
}: UnderstandingBriefPanelProps) {
  const [expanded, setExpanded] = useState(false);
  // 状態が未取得のあいだは何も主張しない。ここで空表示に倒すと、読み込み中の
  // 一瞬だけ「まだ理解を構築していません」という誤った事実を出してしまう。
  if (!state) return null;
  const display: BriefDisplay = BRIEF_DISPLAY_BY_STATE[state];

  if (display === "empty") {
    return (
      <Card data-testid="understanding-brief" data-display="empty">
        <CardContent className="py-4 space-y-1 text-sm">
          <p className="font-medium">現在のシステム理解</p>
          <p className="text-xs text-muted-foreground" data-testid="brief-empty-note">
            まだ理解を構築していません。インタビューを開始すると、ドキュメントとコードから
            Vision・System Purpose・主要機能を読み取り、ここに表示します。
          </p>
          {primaryAction}
        </CardContent>
      </Card>
    );
  }

  if (!brief) {
    // 要約そのものを取得できていない (読み込み中、または古い Control Server で
    // エンドポイントが無い)。ここで主操作と全文ツリーを落とすと、要約 API が
    // 落ちただけで状態の主操作に到達できなくなる -- 要約は判断の補助であって、
    // ワークフローの前提ではない。欠けている事実は隠さずに書く。
    return (
      <Card data-testid="understanding-brief" data-display="unavailable">
        <CardContent className="py-4 space-y-2 text-sm">
          <p className="font-medium">現在のシステム理解</p>
          <p className="text-xs text-muted-foreground" data-testid="brief-unavailable-note">
            要約と進行可否を取得できませんでした。下の詳細から内容を確認してください。
          </p>
          {fullTree}
          {primaryAction}
        </CardContent>
      </Card>
    );
  }

  if (display === "building") {
    // 骨格だけを出す。ここで編集・確定できるようには見せない。
    //
    // `W1` は「システムが何かを実行中」であって「理解を作り直し中」とは限ら
    // ない (提案生成・差分生成も `W1` を出す)。理解が実際に変わるかどうかは
    // サーバーの Readiness (`building` か否か) が答えているので、見出しも
    // それに従う -- 提案生成中に「理解を更新しています」と出すのは、
    // ワークフロー状態を Readiness として読ませることになる。
    const rebuilding = brief.readiness_state === "building";
    return (
      <Card data-testid="understanding-brief" data-display="building">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {rebuilding
              ? brief.built
                ? "現在のシステム理解を更新しています"
                : "システム理解を構築しています"
              : "システムが処理を実行しています"}
          </CardTitle>
          <CardDescription>
            {rebuilding
              ? "Vision・System Purpose・主要機能を読み直しています。完了するまで確認・確定はできません。"
              : "現在のシステム理解は変わりません。処理が終わるまで確認・確定はできません。"}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <ReadinessSummary brief={brief} />
          {rebuilding
            ? brief.built && (
                <p
                  className="text-xs text-muted-foreground"
                  data-testid="brief-building-revision-note"
                >
                  表示中の内容は更新前の理解です
                  {brief.revision_id != null ? ` (revision ${brief.revision_id})` : ""}
                  {brief.snapshot_id != null ? ` / snapshot ${brief.snapshot_id}` : ""}。
                </p>
              )
            : brief.built && <CompactBody brief={brief} />}
        </CardContent>
      </Card>
    );
  }

  if (!brief.built) {
    // 構築中でも W0 でもないのに理解が無い -- ゼロベース (自動では理解を
    // 組み立てられず、質問で進める) の状態。ここで架空の Vision / Purpose を
    // 出さないのは W0 と同じで、進行可否だけは判断材料として出す。
    return (
      <Card data-testid="understanding-brief" data-display="empty">
        <CardContent className="py-4 space-y-2 text-sm">
          <p className="font-medium">現在のシステム理解</p>
          <p className="text-xs text-muted-foreground" data-testid="brief-empty-note">
            構造化されたシステム理解はまだありません。以降は質問への回答から組み立てます。
          </p>
          <ReadinessSummary brief={brief} />
          {primaryAction}
        </CardContent>
      </Card>
    );
  }

  const showFull = display === "full" || expanded;

  return (
    <Card data-testid="understanding-brief" data-display={display}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">現在のシステム理解</CardTitle>
        <CardDescription>
          {display === "full"
            ? "この内容が今回の対象として正しいかを判断してください。"
            : "確認済みの理解の要点です。詳細は展開して確認できます。"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ReadinessSummary brief={brief} />
        <ChangesSinceConfirmation brief={brief} />

        <div id="brief-body" className="space-y-4">
          {showFull ? (
            <>
              <VisionBlock brief={brief} />
              <PurposeBlock brief={brief} />
              <CapabilitiesBlock brief={brief} />
              <KeyUnconfirmedBlock brief={brief} />
              <DetailEntry brief={brief} fullTree={fullTree} />
            </>
          ) : (
            <CompactBody brief={brief} />
          )}
        </div>

        {primaryAction}

        {display === "compact" && (
          <button
            type="button"
            className="text-xs text-primary underline underline-offset-2"
            onClick={() => setExpanded(e => !e)}
            aria-expanded={expanded}
            aria-controls="brief-body"
            data-testid="brief-expand-toggle"
          >
            {expanded ? "要点だけ表示する" : "理解の全体を開く"}
          </button>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * W3〜W7 のコンパクト版。主作業を妨げない量にしつつ、Vision / Purpose /
 * Readiness / 変更有無は必ず読めるようにする (#354 の画面幅対応)。
 */
function CompactBody({ brief }: { brief: UnderstandingBriefOut }) {
  const capabilityNames = brief.core_capabilities
    .slice(0, brief.core_capability_initial_count)
    .map(c => c.name);
  return (
    <dl className="space-y-2 text-xs" data-testid="brief-compact-body">
      <div>
        <dt className="font-semibold text-muted-foreground">Vision</dt>
        <dd className="break-words" data-testid="brief-compact-vision">
          {brief.vision ? (
            <>
              {brief.vision.name}
              <Badge
                variant={CONFIRMATION_VARIANT[brief.vision.confirmation] ?? "outline"}
                className="ml-1 font-normal"
              >
                <span className="sr-only">確認状態: </span>
                {brief.vision.confirmation_label
                  || CONFIRMATION_FALLBACK[brief.vision.confirmation]}
              </Badge>
            </>
          ) : (
            "推定できていません"
          )}
        </dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">System Purpose</dt>
        <dd className="break-words" data-testid="brief-compact-purpose">
          {brief.system_purpose.length > 0
            ? brief.system_purpose.map(p => p.name).join(" / ")
            : "抽出できていません"}
        </dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">主要機能</dt>
        <dd className="break-words" data-testid="brief-compact-capabilities">
          {capabilityNames.length > 0 ? capabilityNames.join("、") : "抽出できていません"}
          {brief.core_capabilities.length > capabilityNames.length
            ? ` ほか ${brief.core_capabilities.length - capabilityNames.length} 件`
            : ""}
        </dd>
      </div>
    </dl>
  );
}
