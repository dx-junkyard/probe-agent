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
  JointUnderstandingAdoptionState,
  JointUnderstandingClaimKind,
  JointUnderstandingExplorationSourceKind,
  JointUnderstandingFailureClass,
  JointUnderstandingFindingOut,
  JointUnderstandingOutcomeClass,
  JointUnderstandingOutcome,
  JointUnderstandingPremiseReason,
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

// Issue #337: one message per finite premise reason, split by recovery path.
// 「スナップショットが変わった」だけでは、前提そのものが消えた場合(再調査でも
// 戻せない)と、そもそも比較できる前提を記録していなかった場合を説明できない。
const PREMISE_REASON_LABELS: Record<JointUnderstandingPremiseReason, string> = {
  premise_not_captured:
    "この対話は前提の記録が始まる前に開かれたため、前提を照合できません。新しく開き直してください",
  premise_incomplete:
    "前提を比較できる情報が揃っていません(pin されたスナップショットがありません)。新しく開き直してください",
  pinned_snapshot_removed:
    "調査対象だったスナップショットが失われました。現在の状態に対して開き直してください",
  origin_removed: "元の確認項目が削除されました。現在の状態に対して開き直してください",
  origin_superseded: "元の確認項目が作り直されました。再調査が必要です",
  pinned_commit_changed: "調査したコミットから変わりました。再調査が必要です",
  origin_content_changed: "元の確認項目の内容が変わりました。再調査が必要です",
  capability_scope_changed: "確定済みの Capability の範囲が変わりました。再調査が必要です",
  linked_intent_changed: "紐づく意図が変わりました。再調査が必要です",
};

// Issue #339: 「調査の限界」と「実行の失敗」は開発者にとって意味が正反対で、
// 次にできることも違う。限界は根拠付きの結果(もっと読めば分かるかもしれない)、
// 失敗は結果が無い状態(直してからやり直す)。同じ「失敗しました」で括ると
// この区別が消える。
const OUTCOME_CLASS_LABELS: Record<JointUnderstandingOutcomeClass, string> = {
  answered: "調査で答えが出ました",
  research_limitation: "調査したが確定できませんでした",
  execution_failure: "調査を実行できませんでした",
};

const FAILURE_CLASS_LABELS: Record<JointUnderstandingFailureClass, string> = {
  config_invalid: "推論モデルの設定を確認してください",
  snapshot_unavailable: "固定したスナップショットを読めませんでした",
  api_failure: "モデル API の呼び出しに失敗しました。再試行できます",
  schema_invalid: "モデルの応答形式が不正でした。再試行できます",
  timeout: "調査の時間上限に達して中断しました",
};

const SOURCE_KIND_LABELS: Record<JointUnderstandingExplorationSourceKind, string> = {
  path_name: "ファイル名",
  symbol_index: "シンボル索引",
  entrypoint_index: "入口の索引",
  file_content: "ファイル内容",
  dependency: "依存関係",
  call_graph: "呼び出し関係",
  git_history: "変更履歴",
  runtime_facts: "実行時の記録",
};

const ADOPTION_STATE_LABELS: Record<JointUnderstandingAdoptionState, string> = {
  provisional: "暫定採用中(事実ではありません)",
  reconfirmation_required: "前提が変わりました。再確認が必要です",
  basis_withdrawn: "根拠にした仮説が調査側で訂正されました",
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
  // Issue #337: the developer's stated judgement, required on every close.
  const [judgement, setJudgement] = useState("");

  if (detail.isLoading) {
    return <p className="text-xs text-muted-foreground">共同理解セッションを読み込んでいます…</p>;
  }
  if (!detail.data) {
    return <p className="text-xs text-muted-foreground">共同理解セッションを表示できません。</p>;
  }

  const {
    session,
    findings,
    investigation_rounds: rounds,
    translations,
    reflux: refluxed,
    hypothesis_adoptions: adoptions,
    available_actions,
  } = detail.data;
  const latest: JointUnderstandingTranslationOut | undefined = translations[translations.length - 1];
  const findingById = new Map(findings.map(f => [f.id, f]));
  const investigationFindings = findings.filter(f => f.origin_role === "investigation");
  const isOpen = session.status === "open";

  const firstLayer = (latest?.statements ?? []).filter(s => FIRST_LAYERS.includes(s.layer));
  const secondLayer = (latest?.statements ?? []).filter(s => SECOND_LAYERS.includes(s.layer));
  // 例外の先出し: 前提が古い / 矛盾 / 未解決点があるときは第2層を初期表示する。
  // Issue #337: any verdict other than 'current' means the ground moved,
  // disappeared, or was never comparable -- all three block adoption/decision.
  const premiseBlocked = session.premise_state !== "current";
  const hasException =
    premiseBlocked ||
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

  // Issue #337: an adoption may only rest on a CURRENT INVESTIGATION hypothesis
  // -- one the investigation has not itself corrected, that is not mock output,
  // and that carries a verified run plus evidence. A developer's own hunch and a
  // superseded hypothesis are both refused by the server, so offering them here
  // would only produce a 422 the developer cannot act on.
  const supersededIds = new Set(
    findings
      .map(f => f.supersedes_finding_id)
      .filter((id): id is number => id !== null),
  );
  const isCurrentBasis = (f: JointUnderstandingFindingOut) =>
    !supersededIds.has(f.id) && !f.is_mock;
  const adoptableHypotheses = findings.filter(
    f =>
      isCurrentBasis(f) &&
      f.origin_role === "investigation" &&
      f.claim_kind === "hypothesis" &&
      f.intelligence_run_id !== null &&
      (f.evidence.length > 0 || f.runtime_evidence.length > 0),
  );

  const closeWith = async (
    outcome: JointUnderstandingOutcome,
    actionKind?: JointUnderstandingActionKind,
  ) => {
    const basis =
      outcome === "hypothesis_adopted"
        ? adoptableHypotheses.map(f => f.id)
        : outcome === "decided"
          ? findings.filter(isCurrentBasis).map(f => f.id)
          : [];
    const reason = judgement.trim();
    if (!reason) {
      // The server requires it; saying so here beats a 422 the developer has to
      // decode. A close is the manual decision record of this conversation.
      toast.error("判断の内容を記入してください");
      return;
    }
    try {
      if (actionKind) {
        await recordAction.mutateAsync({ juId, actionKind });
      }
      await close.mutateAsync({ juId, outcome, outcomeFindingIds: basis, reason });
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
        {premiseBlocked && (
          <span
            className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-800"
            data-testid="ju-premise-not-current"
          >
            {session.premise_reason
              ? PREMISE_REASON_LABELS[session.premise_reason]
              : "前提が現在の状態と一致していません。再調査が必要です"}
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
                {round.sources.length > 0 && (
                  <p data-testid={`ju-round-sources-${round.round_index}`}>
                    探索源:{" "}
                    {round.sources
                      .map(source =>
                        `${SOURCE_KIND_LABELS[source.source_kind]} ${source.candidates_found}件`
                        + `(${source.revision.slice(0, 8)})`
                        + (source.error_details ? " ※取得失敗" : ""),
                      )
                      .join(" / ")}
                  </p>
                )}
                <p data-testid={`ju-round-outcome-${round.round_index}`}>
                  {OUTCOME_CLASS_LABELS[round.outcome_class]}
                  {round.failure_class
                    ? ` — ${FAILURE_CLASS_LABELS[round.failure_class]}`
                    : ""}
                </p>
                {round.error_details && <p className="text-red-700">{round.error_details}</p>}
              </div>
            ))
          )}
          {adoptions.length > 0 && (
            <ul className="space-y-1" data-testid="ju-adoptions">
              {adoptions.map(adoption => (
                <li key={adoption.id}>
                  暫定採用した仮説 #{adoption.finding_id}: {ADOPTION_STATE_LABELS[adoption.state]}
                  {adoption.adopted_by_username ? `(${adoption.adopted_by_username})` : ""}
                </li>
              ))}
            </ul>
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
          <label className="block space-y-1">
            <span className="text-xs text-muted-foreground">
              判断の内容(この対話を終える操作すべてで必須)
            </span>
            <textarea
              className="w-full rounded border border-muted-foreground/30 bg-background p-2 text-xs"
              rows={2}
              value={judgement}
              onChange={event => setJudgement(event.target.value)}
              placeholder="何をどう判断したかを書いてください"
              data-testid="ju-judgement"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            {available_actions.map(actionKind => {
              const entry = menu.find(m => m.action_kind === actionKind);
              return (
                <Button
                  key={actionKind}
                  size="sm"
                  variant="outline"
                  title={
                    actionKind === "adopt_hypothesis" && adoptableHypotheses.length === 0
                      ? "暫定採用できる調査仮説がまだありません。先に調査してください"
                      : entry?.what_changes
                  }
                  disabled={
                    investigate.isPending ||
                    translate.isPending ||
                    (actionKind === "adopt_hypothesis" && adoptableHypotheses.length === 0)
                  }
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
