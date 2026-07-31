// Epic #328(Phase E / Issue #333): 共同理解パネル。
//
// 「わからない」は終端回答ではなく、AI と開発者が一緒に状況理解を作る工程の
// 開始点として扱う。このパネルは 1 つの共同理解セッションを 4 段階で表示する。
//
//   第1層 目的と影響   : 何のための仕組みで、誰にどう影響するか(通訳の出力)
//   第2層 理由         : 目的とのギャップ・一貫性・判断の分かれ目、未解決点
//   第3層 根拠         : 元になった Finding(事実 / 推論 / 仮説 / 不明 / 矛盾)
//   第4層 調査詳細     : 読んだファイル、未読候補、停止理由、調査 run
//
// 内部名称は隠さない — 第1層に出さないだけで、第3〜4層で必ず開示する。
// どの操作も元の確認項目(Q&A / Intent / Review item / Inquiry)を書き換えない。

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  useCloseJointUnderstanding,
  useHoldJointUnderstanding,
  useInvestigateJointUnderstanding,
  useJointUnderstandingDetail,
  useRecordJointUnderstandingAction,
  useRefluxJointUnderstanding,
  useResumeJointUnderstanding,
  useTranslateJointUnderstanding,
} from "@/api/hooks";
import type {
  JointUnderstandingActionKind,
  JointUnderstandingClaimKind,
  JointUnderstandingFindingOut,
  JointUnderstandingOutcome,
  JointUnderstandingStatementLayer,
  JointUnderstandingStopReason,
  JointUnderstandingTranslationOut,
} from "@/api/types";

// サーバの有限語彙は英語のまま保持し、表示ラベルだけ日本語にする(Issue #266)。
// サーバが action_menu を返したときはそちらを優先し、これは未翻訳状態での
// フォールバック(規約どおり日本語)。
const ACTION_LABELS: Record<JointUnderstandingActionKind, string> = {
  request_investigation: "さらに調査する",
  explain_reasoning: "判断の理由を説明してもらう",
  compare_options: "選択肢を比較する",
  adopt_hypothesis: "仮説を暫定的に採用する",
  revise_intent: "意図を修正する",
  hold: "今は保留する",
  handoff: "他の人に引き継ぐ",
  decide: "判断を確定する",
};

const CLAIM_KIND_LABELS: Record<JointUnderstandingClaimKind, string> = {
  fact: "事実",
  inference: "推論",
  hypothesis: "仮説",
  unknown: "不明",
  conflict: "矛盾",
};

const CLAIM_KIND_STYLES: Record<JointUnderstandingClaimKind, string> = {
  fact: "bg-emerald-500/15 text-emerald-800",
  inference: "bg-sky-500/15 text-sky-800",
  hypothesis: "bg-amber-500/15 text-amber-800",
  unknown: "bg-muted text-muted-foreground",
  conflict: "bg-red-500/15 text-red-800",
};

const STOP_REASON_LABELS: Record<JointUnderstandingStopReason, string> = {
  answered: "調査で答えが出ました",
  budget_exhausted: "調査の上限に達しました",
  no_new_evidence: "新しく読める材料がありませんでした",
  unresolved: "特定できませんでした",
  failed: "調査に失敗しました",
};

const OUTCOME_LABELS: Record<JointUnderstandingOutcome, string> = {
  understood: "理解した",
  doubt_resolved: "疑問が解消した",
  hypothesis_adopted: "仮説を暫定採用した",
  decided: "正式に判断した",
  handed_off: "引き継いだ",
  abandoned: "中断した",
};

const FIRST_LAYERS: JointUnderstandingStatementLayer[] = ["purpose", "impact"];
const SECOND_LAYERS: JointUnderstandingStatementLayer[] = ["gap", "consistency", "decision"];

function ClaimKindBadge({ kind }: { kind: JointUnderstandingClaimKind }) {
  return (
    <span
      className={`rounded px-1 text-[10px] ${CLAIM_KIND_STYLES[kind]}`}
      data-testid={`ju-claim-kind-${kind}`}
    >
      {CLAIM_KIND_LABELS[kind]}
    </span>
  );
}

function FindingCard({ finding }: { finding: JointUnderstandingFindingOut }) {
  return (
    <div
      className="rounded border border-muted-foreground/20 p-2 space-y-1 text-xs"
      data-testid={`ju-finding-${finding.id}`}
    >
      <div className="flex flex-wrap items-center gap-1">
        <ClaimKindBadge kind={finding.claim_kind} />
        {finding.origin_role === "developer" && (
          <span className="rounded bg-muted px-1 text-[10px]">あなたの記録</span>
        )}
        {finding.is_mock && (
          <span className="rounded bg-orange-500/20 px-1 text-[10px] text-orange-800" data-testid="ju-mock-badge">
            mock 出力
          </span>
        )}
      </div>
      <p>{finding.statement}</p>
      {finding.competing_explanations.length > 0 && (
        <p className="text-muted-foreground">
          競合する説明: {finding.competing_explanations.join(" / ")}
        </p>
      )}
      {finding.refutation_conditions.length > 0 && (
        <p className="text-muted-foreground">
          反証条件: {finding.refutation_conditions.join(" / ")}
        </p>
      )}
      {finding.uncertainty && (
        <p className="text-amber-700">未確認: {finding.uncertainty}</p>
      )}
      {finding.evidence.length > 0 && (
        <ul className="font-mono text-[10px] text-muted-foreground">
          {finding.evidence.map((e, index) => (
            <li key={`${e.path}-${index}`}>
              {e.path}:{e.start_line}-{e.end_line}
              {e.summary ? ` — ${e.summary}` : ""}
            </li>
          ))}
        </ul>
      )}
      {finding.runtime_evidence.length > 0 && (
        <ul className="font-mono text-[10px] text-muted-foreground">
          {finding.runtime_evidence.map((e, index) => (
            <li key={`${e.component_id}-${index}`}>
              runtime:{e.component_id} [{e.runtime_check}]
              {e.summary ? ` — ${e.summary}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function JointUnderstandingPanel({
  sessionId,
  juId,
  onClosed,
}: {
  sessionId: number;
  juId: number;
  onClosed?: () => void;
}) {
  const detail = useJointUnderstandingDetail(juId);
  const investigate = useInvestigateJointUnderstanding(sessionId);
  const translate = useTranslateJointUnderstanding(sessionId);
  const recordAction = useRecordJointUnderstandingAction(sessionId);
  const reflux = useRefluxJointUnderstanding(sessionId);
  const close = useCloseJointUnderstanding(sessionId);
  const hold = useHoldJointUnderstanding(sessionId);
  const resume = useResumeJointUnderstanding(sessionId);

  const [showReasons, setShowReasons] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [menu, setMenu] = useState<{ action_kind: JointUnderstandingActionKind; label: string; what_changes: string }[]>([]);

  if (detail.isLoading) {
    return <p className="text-xs text-muted-foreground">共同理解セッションを読み込んでいます…</p>;
  }
  if (!detail.data) {
    return <p className="text-xs text-muted-foreground">共同理解セッションを表示できません。</p>;
  }

  const { session, findings, investigation_rounds: rounds, translations, reflux: refluxed, available_actions } = detail.data;
  const latest: JointUnderstandingTranslationOut | undefined = translations[translations.length - 1];
  const findingById = new Map(findings.map(f => [f.id, f]));
  const investigationFindings = findings.filter(f => f.origin_role === "investigation");
  const isOpen = session.status === "open";

  const firstLayer = (latest?.statements ?? []).filter(s => FIRST_LAYERS.includes(s.layer));
  const secondLayer = (latest?.statements ?? []).filter(s => SECOND_LAYERS.includes(s.layer));
  // 例外の先出し: 前提が古い / 矛盾 / 未解決点があるときは第2層を初期表示する。
  const hasException =
    session.premise_state === "stale" ||
    (latest?.open_unknowns.length ?? 0) > 0 ||
    findings.some(f => f.claim_kind === "conflict");

  const runAction = async (actionKind: JointUnderstandingActionKind) => {
    try {
      await recordAction.mutateAsync({ juId, actionKind });
      if (actionKind === "request_investigation") {
        const result = await investigate.mutateAsync({ juId });
        toast.success(STOP_REASON_LABELS[result.stop_reason]);
      } else if (actionKind === "explain_reasoning" || actionKind === "compare_options") {
        const result = await translate.mutateAsync({ juId });
        setMenu(result.action_menu);
      } else if (actionKind === "hold") {
        await hold.mutateAsync({ juId });
      }
    } catch (error) {
      // 選んだ行動の監査は先に残す。実行に失敗しても対話は open のまま続けられる。
      toast.error(error instanceof Error ? error.message : "操作に失敗しました");
    }
  };

  const closeWith = async (
    outcome: JointUnderstandingOutcome,
    actionKind?: JointUnderstandingActionKind,
  ) => {
    const basis =
      outcome === "hypothesis_adopted"
        ? findings.filter(f => f.claim_kind === "hypothesis").map(f => f.id)
        : outcome === "decided"
          ? findings.map(f => f.id)
          : [];
    try {
      if (actionKind) {
        await recordAction.mutateAsync({ juId, actionKind });
      }
      await close.mutateAsync({ juId, outcome, outcomeFindingIds: basis });
      onClosed?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "この状態では確定できません");
    }
  };

  return (
    <div className="rounded border border-sky-500/40 bg-sky-500/5 p-3 space-y-3" data-testid="joint-understanding-panel">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">一緒に状況を確かめる</p>
        {session.trigger === "unknown_answer" && (
          <span className="rounded bg-muted px-1 text-[10px]" data-testid="ju-trigger-unknown">
            「わからない」から開始
          </span>
        )}
        {session.premise_state === "stale" && (
          <span className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-800" data-testid="ju-premise-stale">
            前提のスナップショットが変わりました。再調査が必要です
          </span>
        )}
        {session.outcome && (
          <span
            className={`rounded px-1 text-[10px] ${
              session.outcome_is_provisional
                ? "bg-amber-500/20 text-amber-900"
                : "bg-emerald-500/20 text-emerald-900"
            }`}
            data-testid="ju-outcome-badge"
          >
            {OUTCOME_LABELS[session.outcome]}
            {session.outcome_is_provisional ? "(暫定・事実ではありません)" : ""}
          </span>
        )}
      </div>

      <p className="text-xs text-muted-foreground">{session.question_text}</p>

      {/* 第1層: 目的と影響 */}
      {latest ? (
        <div className="space-y-1" data-testid="ju-layer-purpose">
          {latest.is_mock && (
            <span className="rounded bg-orange-500/20 px-1 text-[10px] text-orange-800" data-testid="ju-translation-mock-badge">
              mock 出力
            </span>
          )}
          <p className="text-sm">{latest.purpose_summary}</p>
          <ul className="list-disc pl-4 text-xs">
            {firstLayer.map(statement => (
              <li key={statement.finding_id}>{statement.text}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground" data-testid="ju-not-translated">
          まだ説明は作られていません。「さらに調査する」で材料を集めてから、
          「判断の理由を説明してもらう」を選んでください。
        </p>
      )}

      {/* 第2層: 理由 */}
      {(showReasons || hasException) && latest && (
        <div className="space-y-1 text-xs" data-testid="ju-layer-reasons">
          <ul className="list-disc pl-4">
            {secondLayer.map(statement => (
              <li key={statement.finding_id}>{statement.text}</li>
            ))}
          </ul>
          {latest.open_unknowns.length > 0 && (
            <p className="text-amber-700" data-testid="ju-open-unknowns">
              まだ分かっていないこと: {latest.open_unknowns.join(" / ")}
            </p>
          )}
          {latest.options.length > 0 && (
            <div className="space-y-1" data-testid="ju-options">
              {latest.options.map(option => (
                <div key={option.label} className="rounded border border-muted-foreground/20 p-2">
                  <p className="font-medium">{option.label}</p>
                  <p>変わること: {option.what_changes}</p>
                  {option.tradeoffs && <p className="text-muted-foreground">代償: {option.tradeoffs}</p>}
                </div>
              ))}
            </div>
          )}
          {latest.ask_developer && latest.decision_question && (
            <p className="font-medium" data-testid="ju-decision-question">
              判断していただきたいこと: {latest.decision_question}
            </p>
          )}
        </div>
      )}

      {/* 第3層: 根拠 */}
      {showEvidence && (
        <div className="space-y-2" data-testid="ju-layer-evidence">
          {findings.length === 0 ? (
            <p className="text-xs text-muted-foreground">まだ根拠は記録されていません。</p>
          ) : (
            findings.map(finding => <FindingCard key={finding.id} finding={finding} />)
          )}
          {latest && (
            <p className="text-[10px] text-muted-foreground" data-testid="ju-traceability">
              説明の各文は{" "}
              {latest.statements
                .flatMap(s => s.supports_finding_ids)
                .map(id => `#${id}`)
                .join(", ") || "—"}{" "}
              の根拠に基づいています
              {latest.statements.some(s =>
                s.supports_finding_ids.some(id => !findingById.has(id)),
              )
                ? "(一部の根拠を表示できません)"
                : ""}
            </p>
          )}
        </div>
      )}

      {/* 第4層: 調査詳細 */}
      {showAudit && (
        <div className="space-y-1 text-[10px] text-muted-foreground" data-testid="ju-layer-audit">
          {rounds.length === 0 ? (
            <p>まだ調査は実行されていません。</p>
          ) : (
            rounds.map(round => (
              <div key={round.id} data-testid={`ju-round-${round.round_index}`}>
                <p>
                  第{round.round_index}回: 読んだファイル {round.files_read} 件 / 未読候補{" "}
                  {round.unread_candidates.length} 件 / 調査 run {round.intelligence_run_id ?? "—"}
                  {round.stop_reason ? ` / ${STOP_REASON_LABELS[round.stop_reason]}` : ""}
                </p>
                {round.read_paths.length > 0 && (
                  <p className="font-mono">{round.read_paths.join(", ")}</p>
                )}
                {round.missing_evidence.length > 0 && (
                  <p>不足していた証拠: {round.missing_evidence.join(" / ")}</p>
                )}
                {round.error_details && <p className="text-red-700">{round.error_details}</p>}
              </div>
            ))
          )}
          {refluxed.length > 0 && (
            <p data-testid="ju-reflux-summary">
              システムが確認した事実 {refluxed.length} 件を、回答としてではなく理解へ反映済みです
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2 text-xs">
        <button type="button" className="underline" onClick={() => setShowReasons(v => !v)} data-testid="ju-toggle-reasons">
          {showReasons ? "理由を隠す" : "理由を見る"}
        </button>
        <button type="button" className="underline" onClick={() => setShowEvidence(v => !v)} data-testid="ju-toggle-evidence">
          {showEvidence ? "根拠を隠す" : "根拠を見る"}
        </button>
        <button type="button" className="underline" onClick={() => setShowAudit(v => !v)} data-testid="ju-toggle-audit">
          {showAudit ? "調査詳細を隠す" : "調査詳細を見る"}
        </button>
      </div>

      {session.status === "held" && (
        <Button
          size="sm"
          variant="outline"
          disabled={resume.isPending}
          onClick={() => void resume.mutateAsync({ juId }).catch(error =>
            toast.error(error instanceof Error ? error.message : "再開できませんでした"),
          )}
          data-testid="ju-resume"
        >
          共同理解を再開する
        </Button>
      )}

      {isOpen && (
        <div className="space-y-2" data-testid="ju-action-menu">
          <p className="text-xs font-medium">次にどうしますか</p>
          <div className="flex flex-wrap gap-2">
            {available_actions.map(actionKind => {
              const entry = menu.find(m => m.action_kind === actionKind);
              return (
                <Button
                  key={actionKind}
                  size="sm"
                  variant="outline"
                  title={entry?.what_changes}
                  disabled={investigate.isPending || translate.isPending}
                  onClick={() => {
                    if (actionKind === "adopt_hypothesis") {
                      void closeWith("hypothesis_adopted", actionKind);
                    } else if (actionKind === "decide") {
                      void closeWith("decided", actionKind);
                    } else if (actionKind === "handoff") {
                      void closeWith("handed_off", actionKind);
                    } else {
                      void runAction(actionKind);
                    }
                  }}
                  data-testid={`ju-action-${actionKind}`}
                >
                  {entry?.label ?? ACTION_LABELS[actionKind]}
                </Button>
              );
            })}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" onClick={() => void closeWith("understood")} data-testid="ju-close-understood">
              状況を理解できた
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void closeWith("doubt_resolved")} data-testid="ju-close-doubt-resolved">
              疑問が解消した
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void closeWith("abandoned")} data-testid="ju-close-abandoned">
              この対話を中断する
            </Button>
          </div>
          {investigate.isPending && (
            <p className="text-xs text-muted-foreground" data-testid="ju-investigating">
              関連するコードとテストを確認しています…
            </p>
          )}
          {investigationFindings.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void reflux
                  .mutateAsync({ juId })
                  .then(result =>
                    toast.success(
                      `確認できた事実 ${result.refluxed.length} 件を理解へ反映しました(回答としては記録していません)`,
                    ),
                  )
                  .catch(error =>
                    toast.error(error instanceof Error ? error.message : "反映できませんでした"),
                  );
              }}
              data-testid="ju-reflux"
            >
              確認できた事実を理解へ反映する
            </Button>
          )}
          <p className="text-[10px] text-muted-foreground">
            ここでの操作は元の確認項目には回答しません。項目の回答は元の画面で行ってください。
          </p>
        </div>
      )}
    </div>
  );
}
