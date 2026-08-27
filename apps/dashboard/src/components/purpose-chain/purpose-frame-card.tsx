// Issue #390 §3.1: Overview Level 0 — the Purpose Frame.
//
// Leads the main column, above System Brief (`docs/purpose-chain.md` §3.1):
// the Epic's question 「何のためのシステムか」 must be answerable before
// anything else on the page. Three elements in causal order, then AT MOST
// ONE contextual question — never a list, never a completion percentage,
// never the relation graph, never an AI-invented beneficiary (§3.1's
// explicit "do not show" list).
//
// This is a PROPS-ONLY display component, matching every other Overview card
// (`components/overview/*`) — the page owns the queries, this file only
// renders their result. The frame comes from `overview.purpose_chain`,
// already embedded by `GET /overview` (§1.6), so it can never disagree with
// the rest of the Overview. The single next question is a SEPARATE fetch at
// the page level (`GET /purpose-chain/next-question` — the Overview response
// carries no question, only the Purpose Chain projection) and arrives here
// as `question` / `questionState`.

import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ELEMENT_KIND_HEADING, FRAME_ELEMENT_ORDER } from "./model";
import type { OverviewOut, PurposeElementOut, PurposeQuestionOut } from "@/api/types";

/** `unknown` (読めたが記録がない) と `unavailable` (読めなかった) は別の値で
 * あり、同じ文言で表示しない (§0 invariant 6) — this is the one place that
 * distinction has to survive a missing FRAME SLOT specifically (as opposed
 * to the whole section failing, handled by the caller before this
 * component is even rendered). */
function elementStateNote(element: PurposeElementOut | null): string {
  if (!element) return "取得できません";
  if (element.state === "unavailable") return "取得できません";
  if (element.state === "unknown") return "まだわかっていません";
  return "";
}

/** `/interview` deep link carrying the destination's OWN parameter names
 * (the same #371 rule the rest of the Overview follows via `targetHref`),
 * so navigating there and landing with nothing selected cannot happen. */
export function purposeInterviewHref(sessionId: number | null, needId?: string): string {
  const params = new URLSearchParams();
  if (sessionId != null) params.set("session", String(sessionId));
  if (needId) params.set("purpose_need", needId);
  const qs = params.toString();
  return qs ? `/interview?${qs}` : "/interview";
}

export interface PurposeFrameCardProps {
  overview: OverviewOut;
  /** `null` while loading, still distinguished from "loaded, no question"
   * (`undefined`) by `questionState` below — never inferred from `null`
   * alone (loading vs. answered-none-needed are different §3.4 states). */
  question: PurposeQuestionOut | null;
  questionState: "loading" | "error" | "ready";
}

export function PurposeFrameCard({ overview, question, questionState }: PurposeFrameCardProps) {
  const chain = overview.purpose_chain;
  const degraded = overview.degraded_sections.includes("purpose_chain");
  const sessionId = chain?.session_id ?? overview.interview_session_id ?? null;

  if (degraded || !chain) {
    return (
      <Card data-testid="overview-purpose-frame-unavailable">
        <CardHeader>
          <CardTitle as="h2" className="text-lg">目的の連鎖 (Purpose Chain)</CardTitle>
        </CardHeader>
        <CardContent className="text-base text-muted-foreground">
          <p>対象者・望ましい変化・システムの介入を取得できませんでした。</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="overview-purpose-frame">
      <CardHeader>
        <CardTitle as="h2" className="text-lg">目的の連鎖 (Purpose Chain)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* An ordered list: the DOM order and the causal order are the same,
            so a screen reader gets the same priority as the eye (§3.4). */}
        <ol className="space-y-3">
          {FRAME_ELEMENT_ORDER.map((kind, i) => {
            const element = chain.frame[kind];
            const note = elementStateNote(element);
            return (
              <li key={kind} data-testid={`overview-purpose-element-${kind}`}>
                <div className="flex items-baseline gap-2">
                  <span className="text-xs font-semibold text-muted-foreground">{i + 1}.</span>
                  <h3 className="text-sm font-semibold text-muted-foreground">
                    {ELEMENT_KIND_HEADING[kind]}
                  </h3>
                </div>
                {note ? (
                  // 色だけに頼らない: テキストで明示する (§3.4)。
                  <p
                    className="ml-4 mt-0.5 text-base text-muted-foreground"
                    data-element-state={element?.state ?? "unavailable"}
                  >
                    {note}
                  </p>
                ) : (
                  <>
                    <p className="ml-4 mt-0.5 text-base" data-element-state={element!.state}>
                      {element!.display_statement || element!.statement}
                    </p>
                    {/* 確認状態と出所は**常に**両方出す。
                        当初は未確認のときだけ badge を出していたが、それは
                        「確認済み」を badge の**不在**で表すことになる。読み手は
                        「確認済み」と「表示が出ていないだけ」を区別できず、
                        dogfooding の被験者は実際にここで詰まった —— 3 要素の
                        文言は正しく読み取れたのに、「これは開発者自身が確定した
                        内容なのか AI の仮説なのか」が判断できず確信度が落ちた。
                        出所を出さないことも同じ欠落で、#387 UX原則8 と Epic の
                        受け入れ条件「AI 候補と developer-confirmed intent を
                        混同しない」は Level 0 でも満たす必要がある。 */}
                    <div className="ml-4 mt-1 flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" className="font-normal">
                        <span className="sr-only">確認状態: </span>
                        {element!.confirmation_label}
                      </Badge>
                      <Badge variant="outline" className="font-normal">
                        <span className="sr-only">出所: </span>
                        {element!.provenance_label}
                      </Badge>
                    </div>
                  </>
                )}
              </li>
            );
          })}
        </ol>

        {/* 最重要 unknown があるときだけ、文脈付き質問 1 件 (§3.1). Loading /
            no-question / question / error are four different renders, never
            silence standing in for "still loading". */}
        {questionState === "loading" ? (
          <p className="text-sm text-muted-foreground" data-testid="overview-purpose-question-loading">
            確認が必要な点を調べています…
          </p>
        ) : questionState === "error" ? (
          <p className="text-sm text-muted-foreground" data-testid="overview-purpose-question-error">
            次の質問を取得できませんでした。
          </p>
        ) : question ? (
          <div
            className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm space-y-1 dark:border-amber-800 dark:bg-amber-950/20"
            data-testid="overview-purpose-question"
          >
            <p className="font-medium">{question.prompt}</p>
            <p id={`overview-purpose-question-why-${question.need_id}`} className="text-muted-foreground">
              {question.why_now}
            </p>
            {question.unlocks && (
              <p className="text-muted-foreground">回答すると: {question.unlocks}</p>
            )}
            <Link
              to={purposeInterviewHref(sessionId, question.need_id)}
              className="mt-1 inline-block text-sm text-primary underline underline-offset-2"
              data-testid="overview-purpose-question-cta"
              aria-describedby={`overview-purpose-question-why-${question.need_id}`}
            >
              {question.answerability === "system_researchable"
                ? "調査を依頼する"
                : "1つの質問に答える"}
            </Link>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="overview-purpose-question-none">
            現時点で追加の確認は必要ありません。
          </p>
        )}
        {sessionId == null && (
          <p className="text-sm text-muted-foreground" data-testid="overview-purpose-no-session">
            まだインタビュー セッションがありません。
            <Link to="/interview" className="ml-1 text-primary underline underline-offset-2">
              インタビューを開始する
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
