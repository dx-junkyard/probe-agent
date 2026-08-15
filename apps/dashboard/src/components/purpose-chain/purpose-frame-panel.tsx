// Issue #390 §3.2: Interview Level 1 — the full Purpose Chain panel.
//
// The 3 frame elements and their relations, in vertical semantic order (a
// graph is never forced — `docs/purpose-chain.md` §3.2 and the accessibility
// contract §3.4 both require heading order = causal order and relations not
// conveyed by arrows/lines alone). Each element shows statement / confirmation
// / provenance / relation status / source revision / evidence / staleness;
// the single currently-active adaptive question (§2.4's `select_question`,
// 0 or 1 — never a catalogue) attaches its confirm/correct/わからない/保留
// actions to whichever element or relation it targets, so the panel never
// invents a form for something the server has not decided is worth asking.
//
// Nothing here re-derives a confirmation state, a relation status, a
// resolution level or a recheck reason — every judgement comes from
// `GET /purpose-chain` / `GET /purpose-chain/next-question` verbatim
// (`components/purpose-chain/model.ts` only orders and labels them).

import { useState } from "react";
import { toast } from "sonner";
import { AlertCircle, HelpCircle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateVerificationConcept,
  useConfirmVerificationConcept,
  useLinkOutcomeCriterion,
  useDecidePurposeRelation,
  usePurposeChain,
  usePurposeNextQuestion,
  usePurposeVerificationPrompt,
  usePurposeVerificationState,
  useRecordOutcomeResult,
  useRecordOutcomeUnavailable,
  useRespondPurposeNeed,
} from "@/api/hooks";
import {
  ANSWERABILITY_LABEL,
  ELEMENT_KIND_HEADING,
  FRAME_RELATION_ORDER,
  NEED_STATE_LABEL,
  RELATION_KIND_LABEL,
  RESOLUTION_LEVEL_LABEL,
  STALE_REASON_LABEL,
  additionalInterventionElements,
  capabilityElements,
  capabilityRelation,
  frameRelation,
  frameSlots,
  questionAwaitsHumanDecision,
  questionTargets,
  relationIsDecidable,
} from "./model";
import type {
  PurposeChainOut, PurposeElementOut, PurposeOutcomeCriterionOut,
  PurposeOutcomeEvidenceSource, PurposeQuestionOut, PurposeRelationOut,
} from "@/api/types";

// ── §4.5 検証条件 (Experience / Outcome / Reuse, Issue #391) ───────────────
//
// Deliberately minimal: the normal render is ONE sentence saying nothing is
// needed. Only when the server names a currently-creatable concept
// (`GET /purpose-chain/verification-prompt`) does ONE prompt appear, with
// the minimum form for that concept kind and two ways out (create, or defer
// the underlying need). There is no outcome dashboard, no retention chart,
// no list of past hypotheses here — that is an explicit non-goal (§4.5 /
// docs §5).

const _VERIFICATION_CONCEPT_LABEL: Record<string, string> = {
  experience_hypothesis: "最小の成功体験",
  outcome_criterion: "成果証拠",
  reuse_hypothesis: "再利用の契機",
};

const _OUTCOME_STATE_LABEL: Record<string, string> = {
  proposed: "仮説", confirmed: "確認済み", observed: "観測済み",
  contradicted: "矛盾", not_observed: "未観測", not_computed: "算出不能",
};

function RoutedNeedAction({ question, sessionId }: { question: PurposeQuestionOut; sessionId: number }) {
  const respond = useRespondPurposeNeed(sessionId);
  const routed = question.answerability === "human_judgement" ? question.routed_needs[0] : null;
  if (!routed) return null;
  return (
    <div className="rounded-md border bg-muted/40 p-3 text-sm" data-testid="purpose-routed-need">
      <p className="font-medium">システムで調査できる点</p>
      <p className="text-muted-foreground">{routed.target_label}</p>
      <Button
        size="sm"
        variant="outline"
        disabled={respond.isPending}
        onClick={() => respond.mutate(
          { needId: routed.need_id, responseKind: "investigate" },
          { onSuccess: () => toast.success("調査を依頼しました"), onError: (e) => toast.error(String(e)) },
        )}
        data-testid={`purpose-routed-investigate-${routed.need_id}`}
      >
        Joint Understanding で調査する
      </Button>
    </div>
  );
}

function OutcomeCriterionCard({ row, sessionId }: { row: PurposeOutcomeCriterionOut; sessionId: number }) {
  const confirm = useConfirmVerificationConcept(sessionId);
  const link = useLinkOutcomeCriterion(sessionId);
  const result = useRecordOutcomeResult(sessionId);
  const unavailable = useRecordOutcomeUnavailable(sessionId);
  const [source, setSource] = useState<PurposeOutcomeEvidenceSource>("human_reported");
  const [evidence, setEvidence] = useState("");
  const [synthetic, setSynthetic] = useState(false);
  const [experimentId, setExperimentId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const busy = confirm.isPending || link.isPending || result.isPending || unavailable.isPending;
  const reportError = (e: unknown) => toast.error(String(e));
  const sourceFacts = [
    ["利用者からの報告", row.human_reported_state, row.human_reported_evidence, row.human_reported_is_synthetic],
    ["runtime観測", row.runtime_observation_state, row.runtime_observation_text, row.runtime_observation_is_synthetic],
  ] as const;

  return (
    <div className="rounded-md border p-3 space-y-2" data-testid={`purpose-outcome-${row.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">{row.target_label}: {row.measure}</p>
        <Badge variant="outline">{_OUTCOME_STATE_LABEL[row.state]}</Badge>
        <Badge variant="outline">lineage: {row.lineage_state === "linked" ? "接続済み" : row.lineage_state === "unresolved" ? "関連不明" : "未接続"}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">基準 {row.baseline_value} → 目標 {row.target_value} / {row.observation_window}</p>
      {sourceFacts.map(([label, state, text, isSynthetic]) => (
        <p key={label} className="text-xs" data-testid={`purpose-outcome-source-${row.id}-${label}`}>
          {label}: {state ? _OUTCOME_STATE_LABEL[state] : "記録なし"}
          {isSynthetic ? "（synthetic fixture）" : ""}{text ? ` — ${text}` : ""}
        </p>
      ))}
      {row.state === "proposed" && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => confirm.mutate(
          { kind: "outcome_criterion", id: row.id }, { onError: reportError },
        )}>検証条件を確認する</Button>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs">Experiment ID
          <input className="block w-full rounded-md border bg-transparent px-2 py-1" value={experimentId} onChange={(e) => { setExperimentId(e.target.value); if (e.target.value) setCandidateId(""); }} />
        </label>
        <label className="text-xs">Candidate version ID
          <input className="block w-full rounded-md border bg-transparent px-2 py-1" value={candidateId} onChange={(e) => { setCandidateId(e.target.value); if (e.target.value) setExperimentId(""); }} />
        </label>
      </div>
      <Button size="sm" variant="outline" disabled={busy || (!experimentId && !candidateId)} onClick={() => link.mutate({
        id: row.id,
        experimentId: experimentId ? Number(experimentId) : undefined,
        candidateVersionId: candidateId ? Number(candidateId) : undefined,
      }, { onError: reportError })}>改善候補へ接続する</Button>
      <div className="space-y-2 rounded-md bg-muted/40 p-2">
        <label className="text-xs">証拠の種類
          <select className="ml-2 rounded-md border bg-background px-2 py-1" value={source} onChange={(e) => setSource(e.target.value as PurposeOutcomeEvidenceSource)}>
            <option value="human_reported">利用者からの報告</option><option value="runtime_observed">runtime観測</option>
          </select>
        </label>
        <Textarea rows={2} value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="観測内容、または観測・算出できない理由" data-testid={`purpose-outcome-evidence-${row.id}`} />
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />synthetic fixture</label>
        <div className="flex flex-wrap gap-2">
          {(["supports", "contradicts"] as const).map((verdict) => (
            <Button key={verdict} size="sm" variant="outline" disabled={busy || !evidence.trim()} onClick={() => result.mutate({ id: row.id, source, verdict, evidenceText: evidence, isSynthetic: synthetic }, { onError: reportError })}>
              {verdict === "supports" ? "観測済みとして記録" : "矛盾として記録"}
            </Button>
          ))}
          {(["not_observed", "not_computed"] as const).map((state) => (
            <Button key={state} size="sm" variant="outline" disabled={busy || !evidence.trim()} onClick={() => unavailable.mutate({ id: row.id, source, state, reason: evidence }, { onError: reportError })}>
              {state === "not_observed" ? "未観測として記録" : "算出不能として記録"}
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}

function VerificationStateSection({ sessionId }: { sessionId: number }) {
  const query = usePurposeVerificationState(sessionId);
  const confirm = useConfirmVerificationConcept(sessionId);
  if (query.isLoading) return <p className="text-xs text-muted-foreground">記録済みの検証条件を読み込んでいます…</p>;
  if (query.isError || !query.data) return <p className="text-xs text-destructive" data-testid="purpose-verification-state-error">記録済みの検証条件を取得できませんでした。</p>;
  const data = query.data;
  const hypotheses = [
    ...data.experience_hypotheses.map((row) => ({ ...row, kind: "experience_hypothesis" as const, label: "成功体験" })),
    ...data.reuse_hypotheses.map((row) => ({ ...row, kind: "reuse_hypothesis" as const, label: "再利用契機" })),
  ];
  if (hypotheses.length === 0 && data.outcome_criteria.length === 0) return null;
  return (
    <section className="space-y-2" aria-labelledby="purpose-verification-heading">
      <h3 id="purpose-verification-heading" className="text-sm font-semibold">記録済みの検証条件</h3>
      {hypotheses.map((row) => (
        <div key={`${row.kind}-${row.id}`} className="rounded-md border p-3 text-sm" data-testid={`purpose-hypothesis-${row.kind}-${row.id}`}>
          <p><span className="font-medium">{row.label}: </span>{row.statement}</p>
          <p className="text-xs text-muted-foreground">{row.target_label} / {_OUTCOME_STATE_LABEL[row.state] ?? row.state}</p>
          {row.state === "proposed" && <Button size="sm" variant="outline" disabled={confirm.isPending} onClick={() => confirm.mutate({ kind: row.kind, id: row.id })}>仮説を確認する</Button>}
        </div>
      ))}
      {data.outcome_criteria.map((row) => <OutcomeCriterionCard key={row.id} row={row} sessionId={sessionId} />)}
    </section>
  );
}

function VerificationPromptSection({ sessionId }: { sessionId: number }) {
  const promptQuery = usePurposeVerificationPrompt(sessionId);
  const create = useCreateVerificationConcept(sessionId);
  const respond = useRespondPurposeNeed(sessionId);
  const [statement, setStatement] = useState("");
  const [outcomeFields, setOutcomeFields] = useState({
    measure: "", baseline_value: "", target_value: "", observation_window: "",
  });

  if (promptQuery.isLoading) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="purpose-verification-loading">
        検証条件を確認しています…
      </p>
    );
  }

  if (promptQuery.isError) {
    return (
      <p className="text-xs text-destructive" data-testid="purpose-verification-error">
        検証条件の必要性を取得できませんでした。
      </p>
    );
  }

  const prompt = promptQuery.data;
  if (!prompt) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="purpose-verification-none">
        検証条件はまだ必要ありません。
      </p>
    );
  }

  const isOutcome = prompt.concept_kind === "outcome_criterion";
  const canSubmit = isOutcome
    ? Object.values(outcomeFields).every((v) => v.trim())
    : statement.trim().length > 0;

  const submitCreate = () => {
    create.mutate(
      {
        conceptKind: prompt.concept_kind,
        needId: prompt.need_id,
        fields: isOutcome ? outcomeFields : { statement },
      },
      {
        onSuccess: () => {
          setStatement("");
          setOutcomeFields({ measure: "", baseline_value: "", target_value: "", observation_window: "" });
          toast.success("検証条件を記録しました");
        },
        onError: (e) => toast.error(String(e)),
      },
    );
  };

  const skip = () => {
    respond.mutate(
      { needId: prompt.need_id, responseKind: "defer" },
      { onError: (e) => toast.error(String(e)) },
    );
  };

  const busy = create.isPending || respond.isPending;

  return (
    <div
      className="rounded-md border border-dashed p-3 space-y-2 text-sm"
      data-testid="purpose-verification-prompt"
      data-concept-kind={prompt.concept_kind}
    >
      <p className="text-xs font-semibold text-muted-foreground">
        検証条件の提案 — {_VERIFICATION_CONCEPT_LABEL[prompt.concept_kind]}
      </p>
      <p className="font-medium">{prompt.prompt}</p>
      <p className="text-muted-foreground">{prompt.why_now}</p>
      <p className="text-muted-foreground">止まっている判断: {prompt.blocked_decision}</p>
      <p className="text-xs text-muted-foreground">{prompt.observation_hint}</p>

      {isOutcome ? (
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              ["measure", "何を測るか"],
              ["baseline_value", "今の値"],
              ["target_value", "目指す値"],
              ["observation_window", "観測期間"],
            ] as const
          ).map(([field, label]) => (
            <label key={field} className="space-y-1 text-xs text-muted-foreground">
              {label}
              <input
                className="block w-full rounded-md border bg-transparent px-2 py-1 text-sm"
                value={outcomeFields[field]}
                onChange={(e) => setOutcomeFields((prev) => ({ ...prev, [field]: e.target.value }))}
                data-testid={`purpose-verification-outcome-${field}`}
              />
            </label>
          ))}
        </div>
      ) : (
        <Textarea
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          rows={2}
          placeholder="一文で書き留めてください"
          data-testid="purpose-verification-statement"
        />
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={busy || !canSubmit}
          onClick={submitCreate}
          data-testid="purpose-verification-create"
        >
          検証条件として記録する
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={skip}
          data-testid="purpose-verification-skip"
        >
          今は決めない(保留)
        </Button>
      </div>
    </div>
  );
}

// ── 現在の質問 (§2.4-§2.6) ────────────────────────────────────────────────

/**
 * The ONE currently-active adaptive question, rendered as an action block
 * attached to its target. `system_researchable` never gets a confirm/correct
 * form — §2.3: the answer is expected to come from investigation, not from
 * asking a human who cannot see the code either.
 */
function NeedResponseBlock({
  question, sessionId,
}: {
  question: PurposeQuestionOut;
  sessionId: number;
}) {
  const respond = useRespondPurposeNeed(sessionId);
  const [correcting, setCorrecting] = useState(false);
  const [draft, setDraft] = useState(question.suggested_answer?.text ?? "");

  const submit = (kind: "confirm" | "correct" | "unknown" | "defer" | "investigate", valueText?: string) => {
    respond.mutate(
      { needId: question.need_id, responseKind: kind, valueText },
      {
        onSuccess: () => {
          setCorrecting(false);
          toast.success(
            kind === "confirm" ? "確認しました"
              : kind === "correct" ? "訂正を記録しました"
                : kind === "unknown" ? "わからないと記録しました"
                  : kind === "investigate" ? "調査を依頼しました"
                  : "保留しました",
          );
        },
        onError: (e) => toast.error(String(e)),
      },
    );
  };

  const busy = respond.isPending;
  const askable = questionAwaitsHumanDecision(question);
  // A question already `answered` / `deferred` / `waiting` / `unavailable`
  // is READ-ONLY here -- re-offering "確認する" on something already
  // answered, or "調査を依頼する" on something the server marked
  // unavailable, would let the Dashboard invent an action the persisted
  // state does not support (§3.3 states 9/10 are each their own display,
  // never a generic "still pending" form).
  const settled = question.state !== "available";
  // The server's `respond_to_purpose_need` accepts `confirm` ONLY for a
  // relation target -- an element target reaching this endpoint is, by
  // construction, always `frame_missing` (state == "unknown"): there is no
  // existing row to confirm, only one to create, so `confirm` always 422s
  // `purpose_need_nothing_to_confirm` there. Rendering it anyway would offer
  // a button that can never succeed.
  const isRelationTarget = question.target_kind === "relation";

  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50 p-3 space-y-2 text-sm dark:border-amber-800 dark:bg-amber-950/20"
      data-testid={`purpose-need-${question.need_id}`}
      data-answerability={question.answerability}
      data-need-state={question.state}
    >
      <p className="font-medium">{question.prompt}</p>
      <p className="text-muted-foreground">{question.why_now}</p>
      {question.blocked_decision && (
        <p className="text-muted-foreground">止まっている判断: {question.blocked_decision}</p>
      )}
      {question.unlocks && <p className="text-muted-foreground">回答すると: {question.unlocks}</p>}
      {question.defer_impact && (
        <p className="text-muted-foreground">保留した場合: {question.defer_impact}</p>
      )}
      <p className="text-xs text-muted-foreground" data-testid="purpose-need-answerability">
        {ANSWERABILITY_LABEL[question.answerability]}
      </p>

      {settled ? (
        <p className="text-xs text-muted-foreground" data-testid={`purpose-need-state-${question.need_id}`}>
          {NEED_STATE_LABEL[question.state]}
        </p>
      ) : !askable ? (
        // system_researchable: no confirm/correct form (§2.3 -- the answer
        // is expected from investigation, not from a human who cannot see
        // the code either). `investigate` stays available as an explicit
        // developer REQUEST, distinct from a yes/no question (§2.6).
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => submit("investigate")}
          data-testid={`purpose-need-investigate-${question.need_id}`}
        >
          調査を依頼する
        </Button>
      ) : (
        <div className="space-y-2">
          {isRelationTarget ? (
            // A relation-target need's confirm/correct is the SAME
            // underlying write (`record_relation_decision`) the relation's
            // own Confirm/Reject buttons already perform (§2.6) -- offering
            // a second form here would be two controls for one decision.
            // Only `unknown`/`defer` have no equivalent elsewhere.
            <p className="text-xs text-muted-foreground" data-testid={`purpose-need-relation-note-${question.need_id}`}>
              確認・却下はこの関係の「この関係を確認する」「この関係を却下する」から行えます。
            </p>
          ) : correcting ? (
            <div className="space-y-2">
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={2}
                data-testid={`purpose-need-correct-input-${question.need_id}`}
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={busy || !draft.trim()}
                  onClick={() => submit("correct", draft)}
                  data-testid={`purpose-need-correct-submit-${question.need_id}`}
                >
                  この内容を記録する
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => setCorrecting(false)}>
                  キャンセル
                </Button>
              </div>
            </div>
          ) : (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => { setDraft(question.suggested_answer?.text ?? ""); setCorrecting(true); }}
              data-testid={`purpose-need-correct-${question.need_id}`}
            >
              内容を入力する
            </Button>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => submit("unknown")}
              data-testid={`purpose-need-unknown-${question.need_id}`}
            >
              わからない
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => submit("investigate")}
              data-testid={`purpose-need-investigate-${question.need_id}`}
            >
              調査を依頼する
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => submit("defer")}
              data-testid={`purpose-need-defer-${question.need_id}`}
            >
              後で回答する
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── 要素カード ────────────────────────────────────────────────────────────

function ElementCard({
  kind, headingIndex, headingLabel, element, question, sessionId,
}: {
  kind: string;
  headingIndex: number;
  headingLabel: string;
  element: PurposeElementOut | null;
  question: PurposeQuestionOut | null;
  sessionId: number;
}) {
  const isTarget = element != null && questionTargets(question, "element", element.id);
  const missing = !element || element.state !== "present";
  return (
    <div
      className="rounded-md border p-3 space-y-2"
      data-testid={`purpose-element-${kind}`}
      data-element-state={element?.state ?? "unavailable"}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-muted-foreground">
          {headingIndex}. {headingLabel}
        </h3>
        {element && (
          <div className="flex shrink-0 flex-wrap justify-end gap-1">
            <Badge variant={element.confirmation === "confirmed" ? "success" : "outline"}>
              {element.confirmation !== "confirmed" && (
                <AlertCircle className="mr-1 h-3 w-3" aria-hidden="true" />
              )}
              <span className="sr-only">確認状態: </span>
              {element.confirmation_label}
            </Badge>
            <Badge variant="outline" className="font-normal">
              <span className="sr-only">出所: </span>
              {element.provenance_label}
            </Badge>
            <Badge variant="outline" className="font-normal" data-testid={`purpose-element-resolution-${kind}`}>
              {RESOLUTION_LEVEL_LABEL[element.resolution_level]}
            </Badge>
            {element.is_mock && <Badge variant="warning">モック</Badge>}
          </div>
        )}
      </div>

      {missing ? (
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground" data-testid={`purpose-element-missing-${kind}`}>
          <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
          {!element || element.state === "unavailable"
            ? "取得できませんでした。"
            : "まだわかっていません。"}
          {element?.missing_information && element.missing_information.length > 0 && (
            <span>不足している情報: {element.missing_information.join("、")}</span>
          )}
        </div>
      ) : (
        <p className="text-sm break-words">{element!.statement || element!.display_statement}</p>
      )}

      {element && element.evidence_stale && (
        <p className="text-xs text-amber-700 dark:text-amber-300" data-testid={`purpose-element-stale-${kind}`}>
          コードの断面が変わったため、根拠が古くなっている可能性があります。
        </p>
      )}
      {element && element.evidence.length > 0 && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">根拠を見る</summary>
          <div className="mt-1 space-y-0.5">
            {element.evidence.map((ev, i) => (
              <p key={i} className="font-mono break-all">{JSON.stringify(ev)}</p>
            ))}
          </div>
        </details>
      )}
      {element && element.source_ids.length > 0 && (
        <p className="text-[11px] text-muted-foreground">出典: {element.source_ids.join("、")}</p>
      )}
      {element && (element.intent_revision_id != null || element.understanding_revision_id != null || element.snapshot_id != null) && (
        <p className="text-[11px] text-muted-foreground" data-testid={`purpose-element-revisions-${kind}`}>
          source revision:
          {element.intent_revision_id != null ? ` Intent ${element.intent_revision_id}` : ""}
          {element.understanding_revision_id != null ? ` Understanding ${element.understanding_revision_id}` : ""}
          {element.snapshot_id != null ? ` Snapshot ${element.snapshot_id}` : ""}
        </p>
      )}

      {/* §3.2: 「詳細を追加」ではなく「現在の判断を進めるために確認」として
          提示する — この要素が今の質問の対象のときだけ、その質問の
          確認・訂正・疑問を開くフォームをここに出す。 */}
      {isTarget && question && (
        <div data-testid={`purpose-element-question-${kind}`}>
          <NeedResponseBlock question={question} sessionId={sessionId} />
        </div>
      )}
    </div>
  );
}

// ── relation ─────────────────────────────────────────────────────────────

function RelationRow({
  relation, sessionId, question,
}: {
  relation: PurposeRelationOut | undefined;
  sessionId: number;
  question: PurposeQuestionOut | null;
}) {
  const decide = useDecidePurposeRelation(sessionId);
  const [rationale, setRationale] = useState("");
  const [showForm, setShowForm] = useState<"confirmed" | "rejected" | null>(null);
  const isTarget = relation != null && questionTargets(question, "relation", relation.id);

  if (!relation) {
    return (
      <div className="flex items-center gap-2 pl-3 text-sm text-muted-foreground" data-testid="purpose-relation-missing">
        <span aria-hidden="true">↓</span>
        <span>両端がそろっていないため、関係はまだ判定できません。</span>
      </div>
    );
  }

  const decidable = relationIsDecidable(relation);
  const submit = (decision: "confirmed" | "rejected") => {
    decide.mutate(
      { relationId: relation.id, decision, rationale },
      {
        onSuccess: () => {
          setShowForm(null);
          setRationale("");
          toast.success(decision === "confirmed" ? "関係を確認しました" : "関係を却下しました");
        },
        onError: (e) => toast.error(String(e)),
      },
    );
  };

  return (
    <div
      className="ml-3 flex flex-col gap-1 border-l-2 border-dashed pl-3 py-1 text-sm"
      data-testid={`purpose-relation-${relation.kind}`}
      data-relation-status={relation.status}
      data-recheck-state={relation.recheck_state}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span aria-hidden="true">↓</span>
        <span className="text-muted-foreground">{RELATION_KIND_LABEL[relation.kind]}</span>
        <Badge
          variant={
            relation.status === "confirmed" ? "success"
              : relation.status === "conflicting" ? "destructive"
                : relation.status === "hypothesis" ? "outline"
                  : "secondary"
          }
        >
          <span className="sr-only">関係の状態: </span>
          {relation.status_label}
        </Badge>
        {relation.recheck_state === "stale" && (
          <Badge variant="warning" data-testid={`purpose-relation-recheck-${relation.kind}`}>
            再確認が必要
            {relation.stale_reason && `: ${STALE_REASON_LABEL[relation.stale_reason]}`}
          </Badge>
        )}
      </div>
      {relation.decided_at != null && (
        <p className="text-xs text-muted-foreground">
          {relation.decided_by ?? "不明"} が判断済み
          {relation.rationale && `（理由: ${relation.rationale}）`}
        </p>
      )}
      {decidable && (
        showForm ? (
          <div className="space-y-1">
            <Textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="理由(任意)"
              rows={2}
              data-testid={`purpose-relation-rationale-${relation.kind}`}
            />
            <div className="flex gap-2">
              <Button size="sm" disabled={decide.isPending} onClick={() => submit(showForm)}>
                {showForm === "confirmed" ? "確認を確定する" : "却下を確定する"}
              </Button>
              <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => setShowForm(null)}>
                キャンセル
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => setShowForm("confirmed")}
              data-testid={`purpose-relation-confirm-${relation.kind}`}
            >
              この関係を確認する
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => setShowForm("rejected")}
              data-testid={`purpose-relation-reject-${relation.kind}`}
            >
              この関係を却下する
            </Button>
          </div>
        )
      )}
      {/* This relation is the target of the current adaptive question --
          `NeedResponseBlock` shows only `unknown`/`defer` for a relation
          target (confirm/correct are the two buttons directly above, the
          SAME underlying write, §2.6). */}
      {isTarget && question && (
        <div data-testid={`purpose-relation-question-${relation.kind}`}>
          <NeedResponseBlock question={question} sessionId={sessionId} />
        </div>
      )}
    </div>
  );
}

// ── panel ────────────────────────────────────────────────────────────────

function CapabilitiesSection({ chain, question }: { chain: PurposeChainOut; question: PurposeQuestionOut | null }) {
  const capabilities = capabilityElements(chain);
  if (capabilities.length === 0) return null;
  return (
    <section className="space-y-2" aria-labelledby="purpose-capabilities-heading">
      <h3 id="purpose-capabilities-heading" className="text-xs font-semibold uppercase text-muted-foreground">
        Capabilities
      </h3>
      <div className="space-y-2">
        {capabilities.map((cap) => (
          <div key={cap.id} className="space-y-1">
            <RelationRow relation={capabilityRelation(chain, cap.id)} sessionId={chain.session_id ?? 0} question={question} />
            <ElementCard
              kind={cap.id}
              headingIndex={4}
              headingLabel={cap.statement.split("\n")[0] || "Capability"}
              element={cap}
              question={question}
              sessionId={chain.session_id ?? 0}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

export interface PurposeFramePanelProps {
  sessionId: number | null;
  /** From the Overview's deep link (`?purpose_need=<need_id>`). Only a
   * hint — the server decides the actual fallback via `fallback_reason`
   * (§2.7), never re-checked here. */
  initialNeedId?: string | null;
}

export function PurposeFramePanel({ sessionId, initialNeedId }: PurposeFramePanelProps) {
  const chainQuery = usePurposeChain(sessionId);
  const questionQuery = usePurposeNextQuestion(sessionId, initialNeedId);

  if (!sessionId) return null;

  if (chainQuery.isLoading) {
    return (
      <Card data-testid="purpose-frame-panel-loading">
        <CardContent className="py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Purpose Chain を読み込んでいます…
        </CardContent>
      </Card>
    );
  }

  const chain = chainQuery.data;
  if (chainQuery.isError || !chain) {
    return (
      <Card data-testid="purpose-frame-panel-unavailable">
        <CardHeader>
          <CardTitle className="text-sm">Purpose Chain</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Purpose Chain を取得できませんでした。
        </CardContent>
      </Card>
    );
  }

  const question = questionQuery.data ?? null;
  const slots = frameSlots(chain);
  const extraIntervention = additionalInterventionElements(chain);

  return (
    <Card data-testid="purpose-frame-panel" data-frame-state={chain.frame_state}>
      <CardHeader>
        <CardTitle as="h2" className="text-sm">Purpose Chain — 目的の連鎖</CardTitle>
        <CardDescription>
          対象者と課題から、望ましい変化・システムの介入・Capabilities までを、判断できる根拠つきで確認します。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {chain.degraded_sections.length > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="purpose-frame-degraded">
            一部を取得できませんでした: {chain.degraded_sections.join("、")}
          </p>
        )}
        {questionQuery.isError && (
          <p className="text-xs text-destructive" data-testid="purpose-question-error">
            現在の質問を取得できませんでした。質問なしとは判定していません。
          </p>
        )}
        {question && !questionAwaitsHumanDecision(question) && question.state === "available" && (
          <p className="text-xs text-muted-foreground" data-testid="purpose-frame-routed-note">
            {ANSWERABILITY_LABEL[question.answerability]}
          </p>
        )}
        {question && chain.session_id != null && (
          <RoutedNeedAction question={question} sessionId={chain.session_id} />
        )}
        <ol className="space-y-1">
          {slots.map((slot, i) => (
            <li key={slot.kind}>
              <ElementCard
                kind={slot.kind}
                headingIndex={i + 1}
                headingLabel={ELEMENT_KIND_HEADING[slot.kind]}
                element={slot.element}
                question={question}
                sessionId={chain.session_id ?? 0}
              />
              {i < FRAME_RELATION_ORDER.length && (
                <RelationRow relation={frameRelation(chain, FRAME_RELATION_ORDER[i])} sessionId={chain.session_id ?? 0} question={question} />
              )}
            </li>
          ))}
        </ol>

        {extraIntervention.length > 0 && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">その他のシステムの介入 ({extraIntervention.length} 件)</summary>
            <div className="mt-1 space-y-2">
              {extraIntervention.map((e) => (
                <ElementCard
                  key={e.id}
                  kind={e.id}
                  headingIndex={3}
                  headingLabel="システムの介入(追加)"
                  element={e}
                  question={question}
                  sessionId={chain.session_id ?? 0}
                />
              ))}
            </div>
          </details>
        )}

        <CapabilitiesSection chain={chain} question={question} />

        {/* §4.5 (Issue #391): the ONE verification prompt, or the one-line
            "nothing needed" render. Not tied to a single element card --
            §4.1's need-gated targets can be a relation the element cards
            above already show, so this reads as one section for the whole
            Purpose Frame rather than a duplicate badge per element. */}
        {chain.session_id != null && (
          <>
            <VerificationStateSection sessionId={chain.session_id} />
            <VerificationPromptSection sessionId={chain.session_id} />
          </>
        )}
      </CardContent>
    </Card>
  );
}
