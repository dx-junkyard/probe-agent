// Issue #427 (Epic #427) §9.3/D: the Objective Map's own forms for the
// finite next-step operations THIS Epic owns -- Objective create / revision
// / decision / parent / upstream reference, and Milestone create / revision /
// decision / assessment. `confirm_vision` and `link_requirement_to_feature`
// are deliberately NOT here; those next steps deep-link to the screens that
// already own that surface (Interview / UX Design Studio) rather than
// duplicating it.
//
// The CTA that leads here NAVIGATES (§9.3) -- every write below happens only
// on explicit developer submission. Every decision/assessment sends
// `captured_digest` read from the entity's OWN response
// (`current_revision.content_digest`, §4.2/§10.1) -- never a client-computed
// value -- and a stale-digest 409 renders `StaleDigestNotice`: a recoverable
// message asking the developer to reload and re-read before deciding again,
// never a silent or automatic retry (§B).
//
// UX audit 2026-11-17 D8: decisions go through `DecisionConfirmDialog`, which
// names the subject and its CURRENT state (the server's value). No client
// transition table exists: a refused transition comes back as the server's
// own code and is shown in Japanese with that state alongside. The one rule
// read straight off the server's own field is the Milestone assessment gate
// -- the server refuses an assessment unless `design_status === "confirmed"`
// (§4.3), so before that the assessment buttons are not offered at all; the
// reason is shown instead.

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/api/client";
import { useDirtyGuard } from "./unsaved-work";
import {
  useAddProductMilestoneRevision, useAddProductObjectiveRevision,
  useAddProductObjectiveUpstreamRef, useClearProductObjectiveParent,
  useCreateProductMilestone, useCreateProductObjective,
  useProductMilestoneDetail, useProductObjectiveDetail,
  useRecordProductMilestoneAssessment, useRecordProductMilestoneDecision,
  useRecordProductObjectiveDecision, useSetProductObjectiveParent,
} from "@/api/hooks";
import type {
  ProductMilestoneAssessmentKind, ProductMilestoneDecisionKind,
  ProductMilestoneDetailOut, ProductMilestoneVerificationMethod, ProductObjectiveDecisionKind,
  ProductObjectiveDetailOut, ProductMilestoneRevisionOut, ProductObjectiveRevisionOut, ProductRefKind,
} from "@/api/types";
import {
  DESIGN_STATUS_LABEL, MILESTONE_ACHIEVEMENT_LABEL, MILESTONE_ASSESSABILITY_LABEL,
  MILESTONE_ASSESSMENT_ACTION_LABEL, MILESTONE_DECISION_LABEL,
  OBJECTIVE_DECISION_LABEL, OBJECTIVE_REF_KIND_LABEL, OBJECTIVE_STATE_LABEL,
  REF_RECHECK_STATE_LABEL, REF_RELATION_STATUS_LABEL, REF_TARGET_RESOLUTION_LABEL,
  isStaleDigestErrorCode,
} from "./model";
import {
  DecisionConfirmDialog, RejectedNotice, rejectionFromError, useDecisionDialog, type Rejection,
} from "./decision-confirm";
import { ObjectiveParentPicker, ReferenceTargetPicker } from "./reference-picker";

const OBJECTIVE_DECISION_VALUES: ProductObjectiveDecisionKind[] = [
  "confirm", "activate", "achieve", "reject", "retire", "reinstate",
];
const MILESTONE_DECISION_VALUES: ProductMilestoneDecisionKind[] = [
  "confirm", "reject", "retire", "reinstate",
];
const MILESTONE_ASSESSMENT_VALUES: ProductMilestoneAssessmentKind[] = [
  "met", "not_met", "indeterminate", "withdraw",
];
const VERIFICATION_METHOD_VALUES: ProductMilestoneVerificationMethod[] = [
  "manual_review", "runtime_observation", "external_report", "unavailable",
];
const VERIFICATION_METHOD_LABEL: Record<ProductMilestoneVerificationMethod, string> = {
  manual_review: "人による確認",
  runtime_observation: "実行時の観測",
  external_report: "外部からの報告",
  unavailable: "評価方法は未設定",
};
const OBJECTIVE_REF_KINDS: ProductRefKind[] = [
  "vision_claim", "purpose_element", "purpose_relation", "capability_entity", "stakeholder_need",
];

/** §B: a recoverable rendering of a `*_decision_stale_digest` 409 -- the
 * content changed since this screen last read it, so the developer reloads
 * and re-reads before deciding again. Never a silent or automatic retry with
 * the new digest -- that would defeat the whole point of the gate. */
export function StaleDigestNotice({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className="rounded border border-amber-500/50 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200"
      role="alert"
      data-testid="stale-digest-notice"
    >
      <p>内容が変わったため記録できませんでした。最新の内容を読み込み直してから、あらためて判断してください。</p>
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry} data-testid="stale-digest-reload">
        最新の内容を読み込み直す
      </Button>
    </div>
  );
}

const HUMAN_DECISION_NOTE =
  "判断の記録だけを追加します。実装・publish・他の画面の状態は変わりません。前の判断は消えず、履歴に残ります。";

// --- Objective ---------------------------------------------------------------

export function CreateObjectiveForm({ onCreated }: { onCreated: (objectiveKey: string) => void }) {
  const create = useCreateProductObjective();
  const [key, setKey] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);

  function submit() {
    setRejection(null);
    create.mutate(
      { objective_key: key.trim() },
      {
        onSuccess: (out) => { setKey(""); toast.success("Objective を作成しました"); onCreated(out.objective_key); },
        onError: (error) => setRejection(rejectionFromError(error, "作成できませんでした")),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="create-objective-form">
      <p className="text-xs font-semibold">Objective を作成する</p>
      <div className="flex flex-wrap gap-2">
        <Input
          aria-label="objective_key"
          placeholder="objective_key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="max-w-xs"
        />
        <Button size="sm" variant="outline" disabled={!key.trim() || create.isPending} onClick={submit} data-testid="create-objective-submit">
          作成する
        </Button>
      </div>
      {rejection && <RejectedNotice {...rejection} />}
    </div>
  );
}

/**
 * The revision endpoint appends a FULL snapshot, not a patch: whatever this
 * form does not send is stored as empty. Starting blank therefore made
 * 「1 項目だけ直す」 silently blank out every other field, so the form is
 * seeded from the CURRENT revision and the developer edits from there.
 *
 * `useState` seeds once per mount, which is correct because
 * `objective-map.tsx` remounts this panel per `(system_id, objective_key)`.
 * After a successful submit the fields keep what was just saved -- that IS
 * the current revision.
 */
function ObjectiveRevisionForm({
  objectiveKey, current,
}: {
  objectiveKey: string;
  current: ProductObjectiveRevisionOut | null;
}) {
  const add = useAddProductObjectiveRevision(objectiveKey);
  const [title, setTitle] = useState(current?.title ?? "");
  const [intent, setIntent] = useState(current?.intent ?? "");
  const [contribution, setContribution] = useState(current?.contribution ?? "");
  const [summary, setSummary] = useState(current?.summary ?? "");
  // Dirty against the SEED, not against empty: the form starts pre-filled, so
  // "has content" would report every mounted form as unsaved work.
  useDirtyGuard(
    `objective-revision:${objectiveKey}`,
    title !== (current?.title ?? "")
      || intent !== (current?.intent ?? "")
      || contribution !== (current?.contribution ?? "")
      || summary !== (current?.summary ?? ""),
  );

  function submit() {
    add.mutate(
      { title, intent, contribution, summary },
      {
        onSuccess: () => toast.success("内容を記録しました"),
        onError: (error) => toast.error(rejectionFromError(error, "記録できませんでした").message),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="objective-revision-form">
      <p className="text-xs font-semibold">内容を記録する(新しい版として追記されます)</p>
      <Input aria-label="タイトル" placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea
        aria-label="Vision への意図"
        placeholder="Vision への意図(intent)"
        value={intent}
        onChange={(e) => setIntent(e.target.value)}
        rows={2}
      />
      <Textarea
        aria-label="Vision への貢献"
        placeholder="Vision への貢献(contribution)"
        value={contribution}
        onChange={(e) => setContribution(e.target.value)}
        rows={2}
      />
      <Textarea aria-label="要約" placeholder="要約" value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} />
      <Button size="sm" variant="outline" disabled={add.isPending} onClick={submit} data-testid="objective-revision-submit">
        記録する
      </Button>
    </div>
  );
}

/**
 * §4.4: the Objective's parent. Candidates are this System's Objectives
 * (itself excluded); a cycle further up is the server's to refuse
 * (`product_objective_parent_cycle`), never checked here. Detaching is not a
 * deletion -- the server appends a tombstone carrying the reason, which is why
 * it goes through the confirmation dialog with a reason field.
 */
function ObjectiveParentControls({ objective }: { objective: ProductObjectiveDetailOut }) {
  const set = useSetProductObjectiveParent(objective.objective_key);
  const clear = useClearProductObjectiveParent(objective.objective_key);
  const [parentKey, setParentKey] = useState("");
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [detachOpen, setDetachOpen] = useState(false);
  useDirtyGuard(`objective-parent:${objective.objective_key}`, parentKey !== "" || rationale !== "");

  function submitSet() {
    setRejection(null);
    set.mutate(
      { parent_objective_key: parentKey, rationale },
      {
        onSuccess: () => {
          setParentKey(""); setRationale("");
          toast.success("親 Objective を設定しました");
        },
        onError: (error) => setRejection(rejectionFromError(error, "親を設定できませんでした")),
      },
    );
  }

  function submitClear(reason: string) {
    setRejection(null);
    clear.mutate(
      { rationale: reason },
      {
        onSuccess: () => { setDetachOpen(false); toast.success("親 Objective から切り離しました"); },
        onError: (error) => { setDetachOpen(false); setRejection(rejectionFromError(error, "切り離せませんでした")); },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="objective-parent-controls">
      <p className="text-xs font-semibold">親 Objective</p>
      <p className="text-xs" data-testid="objective-parent-current">
        {objective.parent_objective_key
          ? <>現在の親: <span className="font-mono">{objective.parent_objective_key}</span></>
          : "親はありません(最上位の Objective です)。"}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <ObjectiveParentPicker
          excludeKey={objective.objective_key}
          value={parentKey}
          onChange={setParentKey}
          ariaLabel="親にする Objective"
          testId="objective-parent-select"
        />
      </div>
      <Input aria-label="親を設定する理由" placeholder="理由(任意)" value={rationale} onChange={(e) => setRationale(e.target.value)} />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={!parentKey || set.isPending} onClick={submitSet} data-testid="objective-parent-set">
          {objective.parent_objective_key ? "親を付け替える" : "親を設定する"}
        </Button>
        {objective.parent_objective_key && (
          <Button size="sm" variant="ghost" disabled={clear.isPending} onClick={() => setDetachOpen(true)} data-testid="objective-parent-clear">
            親から切り離す
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        循環する親子関係はサーバーが拒否します。切り離しても履歴は残り、誰がなぜ切り離したかが記録されます。
      </p>
      {rejection && <RejectedNotice {...rejection} testId="objective-parent-rejected" />}
      <DecisionConfirmDialog
        open={detachOpen}
        onOpenChange={setDetachOpen}
        title={`Objective「${objective.title || objective.objective_key}」を親から切り離す`}
        currentStateText={`現在の親: ${objective.parent_objective_key ?? "なし"}`}
        consequence="この Objective を最上位へ戻したことを記録します。親子関係の履歴は削除されません。"
        confirmLabel="切り離す"
        reasonLabel="切り離す理由"
        pending={clear.isPending}
        onConfirm={submitClear}
      />
    </div>
  );
}

/** §4.6: references from this Objective to the Vision and other upstream
 * elements. The kind is chosen first, then the target from THAT kind's own
 * list (UX audit C9). The server captures the digest and resolves the target
 * at read time; nothing is copied into the Objective. */
function ObjectiveUpstreamRefs({ objective }: { objective: ProductObjectiveDetailOut }) {
  const add = useAddProductObjectiveUpstreamRef(objective.objective_key);
  const [refKind, setRefKind] = useState<ProductRefKind>("vision_claim");
  const [targetRef, setTargetRef] = useState("");
  const [note, setNote] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  useDirtyGuard(`objective-upstream-ref:${objective.objective_key}`, targetRef !== "" || note !== "");
  const refs = objective.upstream_refs.filter((r) => r.superseded_by_id === null);

  function submit() {
    setRejection(null);
    add.mutate(
      { ref_kind: refKind, target_ref: targetRef, note },
      {
        onSuccess: () => { setTargetRef(""); setNote(""); toast.success("上流への参照を追加しました"); },
        onError: (error) => setRejection(rejectionFromError(error, "参照を追加できませんでした")),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="objective-upstream-refs">
      <p className="text-xs font-semibold">Vision・上流への参照</p>
      {refs.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="objective-upstream-refs-empty">
          まだ Vision や上流の要素を参照していません。
        </p>
      ) : (
        <ul className="space-y-1 text-xs" data-testid="objective-upstream-ref-list">
          {refs.map((r) => (
            <li key={r.id} className="rounded border p-1.5" data-testid={`objective-upstream-ref-${r.id}`}>
              <div className="flex flex-wrap items-center gap-1">
                <Badge variant="outline">{OBJECTIVE_REF_KIND_LABEL[r.ref_kind]}</Badge>
                <span>{r.target_name ?? r.target_ref}</span>
              </div>
              <div className="mt-0.5 text-muted-foreground">
                {REF_RELATION_STATUS_LABEL[r.relation_status]} / {REF_TARGET_RESOLUTION_LABEL[r.target_resolution]} / {REF_RECHECK_STATE_LABEL[r.recheck_state]}
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="参照の種類"
          value={refKind}
          onChange={(e) => { setRefKind(e.target.value as ProductRefKind); setTargetRef(""); }}
          data-testid="objective-upstream-ref-kind"
        >
          {OBJECTIVE_REF_KINDS.map((k) => <option key={k} value={k}>{OBJECTIVE_REF_KIND_LABEL[k]}</option>)}
        </Select>
        <ReferenceTargetPicker
          kind={refKind}
          value={targetRef}
          onChange={setTargetRef}
          ariaLabel="参照先"
          testId="objective-upstream-ref-target"
        />
      </div>
      <Input aria-label="参照のメモ" placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" variant="outline" disabled={!targetRef || add.isPending} onClick={submit} data-testid="objective-upstream-ref-submit">
        参照を追加する
      </Button>
      {rejection && <RejectedNotice {...rejection} testId="objective-upstream-ref-rejected" />}
    </div>
  );
}

function ObjectiveDecisionControls({
  objective, capturedDigest, onStale,
}: {
  objective: ProductObjectiveDetailOut;
  capturedDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductObjectiveDecision(objective.objective_key);
  const dialog = useDecisionDialog<ProductObjectiveDecisionKind>();
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);
  const stateLabel = OBJECTIVE_STATE_LABEL[objective.objective_state];

  function submit(decision: ProductObjectiveDecisionKind, rationale: string) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { decision, rationale, captured_digest: capturedDigest },
      {
        onSuccess: () => { dialog.close(); toast.success(`「${OBJECTIVE_DECISION_LABEL[decision]}」を記録しました`); },
        onError: (error) => {
          dialog.close();
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした", `現在の状態: ${stateLabel}`));
        },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="objective-decision-controls">
      <p className="text-xs font-semibold">Objective の状態を記録する(人間の判断として記録されます)</p>
      <p className="text-xs" data-testid="objective-decision-current-state">現在の状態: {stateLabel}</p>
      <div className="flex flex-wrap gap-2">
        {OBJECTIVE_DECISION_VALUES.map((d) => (
          <Button
            key={d} size="sm" variant="outline" disabled={record.isPending}
            onClick={() => dialog.open(d)} data-testid={`objective-decision-${d}`}
          >
            {OBJECTIVE_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        記録できるかどうかは現在の状態で決まります。記録できない操作はサーバーが理由を返します。
      </p>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} />}
      {dialog.pendingKind && (
        <DecisionConfirmDialog
          open
          onOpenChange={(open) => { if (!open) dialog.close(); }}
          title={`Objective「${objective.title || objective.objective_key}」を${OBJECTIVE_DECISION_LABEL[dialog.pendingKind]}`}
          currentStateText={`現在の状態: ${stateLabel}`}
          consequence={HUMAN_DECISION_NOTE}
          confirmLabel={OBJECTIVE_DECISION_LABEL[dialog.pendingKind]}
          pending={record.isPending}
          onConfirm={(reason) => submit(dialog.pendingKind!, reason)}
        />
      )}
    </div>
  );
}

/** The selected Objective's work surface: content revision, parent, upstream
 * references and decision controls, all scoped to the ONE Objective this
 * panel was given. Fetches `GET /product-objectives/{key}` directly (§4.2's
 * `current_revision.content_digest`), separate from the Objective Map tree's
 * own summary fetch. */
export function ObjectiveWorkPanel({ objectiveKey }: { objectiveKey: string }) {
  const detail = useProductObjectiveDetail(objectiveKey);

  if (detail.isLoading) {
    return <p className="text-sm text-muted-foreground" data-testid="objective-work-panel-loading">読み込んでいます…</p>;
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm" role="alert" data-testid="objective-work-panel-error">
        <p className="font-medium text-destructive">Objective の詳細を取得できませんでした。</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => detail.refetch()}>再試行</Button>
      </div>
    );
  }

  const objective = detail.data;
  const digest = objective.current_revision?.content_digest ?? "";

  return (
    <div className="space-y-3" data-testid={`objective-work-panel-${objective.objective_key}`}>
      <ObjectiveRevisionForm
        objectiveKey={objective.objective_key}
        current={objective.current_revision}
      />
      <ObjectiveUpstreamRefs objective={objective} />
      <ObjectiveParentControls objective={objective} />
      <ObjectiveDecisionControls objective={objective} capturedDigest={digest} onStale={() => detail.refetch()} />
    </div>
  );
}

// --- Milestone -----------------------------------------------------------------

export function CreateMilestoneForm({
  objectiveKey, onCreated,
}: {
  objectiveKey: string;
  onCreated: (milestoneKey: string) => void;
}) {
  const create = useCreateProductMilestone();
  const [key, setKey] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);

  function submit() {
    setRejection(null);
    create.mutate(
      { objective_key: objectiveKey, milestone_key: key.trim() },
      {
        onSuccess: (out) => { setKey(""); toast.success("Milestone を作成しました"); onCreated(out.milestone_key); },
        onError: (error) => setRejection(rejectionFromError(error, "作成できませんでした")),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="create-milestone-form">
      <p className="text-xs font-semibold">この Objective に Milestone を作成する</p>
      <div className="flex flex-wrap gap-2">
        <Input
          aria-label="milestone_key"
          placeholder="milestone_key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="max-w-xs"
        />
        <Button size="sm" variant="outline" disabled={!key.trim() || create.isPending} onClick={submit} data-testid="create-milestone-submit">
          作成する
        </Button>
      </div>
      {rejection && <RejectedNotice {...rejection} />}
    </div>
  );
}

/** Seeded from the current revision for the same reason as
 * `ObjectiveRevisionForm`: the endpoint appends a full snapshot, so a blank
 * form turns 「1 項目だけ直す」 into 「他を全部空へ戻す」. */
function MilestoneRevisionForm({
  milestoneKey, current,
}: {
  milestoneKey: string;
  current: ProductMilestoneRevisionOut | null;
}) {
  const add = useAddProductMilestoneRevision(milestoneKey);
  const [title, setTitle] = useState(current?.title ?? "");
  const [targetState, setTargetState] = useState(current?.target_state ?? "");
  const [verificationMethod, setVerificationMethod] = useState<ProductMilestoneVerificationMethod>(
    current?.verification_method ?? "manual_review",
  );
  const [verificationNote, setVerificationNote] = useState(current?.verification_note ?? "");
  const [sequenceHint, setSequenceHint] = useState(String(current?.sequence_hint ?? 0));
  const [summary, setSummary] = useState(current?.summary ?? "");
  useDirtyGuard(
    `milestone-revision:${milestoneKey}`,
    title !== (current?.title ?? "")
      || targetState !== (current?.target_state ?? "")
      || verificationMethod !== (current?.verification_method ?? "manual_review")
      || verificationNote !== (current?.verification_note ?? "")
      || sequenceHint !== String(current?.sequence_hint ?? 0)
      || summary !== (current?.summary ?? ""),
  );

  function submit() {
    const parsedSequence = Number.parseInt(sequenceHint, 10);
    add.mutate(
      {
        title, target_state: targetState, verification_method: verificationMethod,
        verification_note: verificationNote,
        sequence_hint: Number.isNaN(parsedSequence) ? 0 : parsedSequence,
        summary,
      },
      {
        onSuccess: () => toast.success("内容を記録しました"),
        onError: (error) => toast.error(rejectionFromError(error, "記録できませんでした").message),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="milestone-revision-form">
      <p className="text-xs font-semibold">内容を記録する(新しい版として追記されます)</p>
      <Input aria-label="タイトル" placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea
        aria-label="到達したと判断できる状態"
        placeholder="到達したと判断できる状態(target_state)"
        value={targetState}
        onChange={(e) => setTargetState(e.target.value)}
        rows={2}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="評価方法"
          value={verificationMethod}
          onChange={(e) => setVerificationMethod(e.target.value as ProductMilestoneVerificationMethod)}
        >
          {VERIFICATION_METHOD_VALUES.map((v) => <option key={v} value={v}>{VERIFICATION_METHOD_LABEL[v]}</option>)}
        </Select>
        <Input
          aria-label="表示順序(sequence_hint)"
          type="number"
          value={sequenceHint}
          onChange={(e) => setSequenceHint(e.target.value)}
          className="max-w-[8rem]"
        />
      </div>
      <Textarea
        aria-label="評価方法の補足"
        placeholder="評価方法の補足(任意)"
        value={verificationNote}
        onChange={(e) => setVerificationNote(e.target.value)}
        rows={2}
      />
      <Textarea aria-label="要約" placeholder="要約" value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} />
      <Button size="sm" variant="outline" disabled={add.isPending} onClick={submit} data-testid="milestone-revision-submit">
        記録する
      </Button>
    </div>
  );
}

function MilestoneDecisionControls({
  milestone, capturedDigest, onStale,
}: {
  milestone: ProductMilestoneDetailOut;
  capturedDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductMilestoneDecision(milestone.milestone_key);
  const dialog = useDecisionDialog<ProductMilestoneDecisionKind>();
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);
  const stateLabel = DESIGN_STATUS_LABEL[milestone.design_status];

  function submit(decision: ProductMilestoneDecisionKind, rationale: string) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { decision, rationale, captured_digest: capturedDigest },
      {
        onSuccess: () => { dialog.close(); toast.success(`「${MILESTONE_DECISION_LABEL[decision]}」を記録しました`); },
        onError: (error) => {
          dialog.close();
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした", `定義の状態: ${stateLabel}`));
        },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="milestone-decision-controls">
      <p className="text-xs font-semibold">Milestone の定義を確定する(人間の判断として記録されます)</p>
      <p className="text-xs" data-testid="milestone-decision-current-state">定義の状態: {stateLabel}</p>
      <div className="flex flex-wrap gap-2">
        {MILESTONE_DECISION_VALUES.map((d) => (
          <Button
            key={d} size="sm" variant="outline" disabled={record.isPending}
            onClick={() => dialog.open(d)} data-testid={`milestone-decision-${d}`}
          >
            {MILESTONE_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} />}
      {dialog.pendingKind && (
        <DecisionConfirmDialog
          open
          onOpenChange={(open) => { if (!open) dialog.close(); }}
          title={`Milestone「${milestone.title || milestone.milestone_key}」の定義を${MILESTONE_DECISION_LABEL[dialog.pendingKind]}`}
          currentStateText={`定義の状態: ${stateLabel}`}
          consequence={`${HUMAN_DECISION_NOTE} 定義の確定は達成の判定ではありません。`}
          confirmLabel={MILESTONE_DECISION_LABEL[dialog.pendingKind]}
          pending={record.isPending}
          onConfirm={(reason) => submit(dialog.pendingKind!, reason)}
        />
      )}
    </div>
  );
}

function MilestoneAssessmentControls({
  milestone, capturedDigest, onStale,
}: {
  milestone: ProductMilestoneDetailOut;
  capturedDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductMilestoneAssessment(milestone.milestone_key);
  const dialog = useDecisionDialog<ProductMilestoneAssessmentKind>();
  const [evidenceNote, setEvidenceNote] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);
  useDirtyGuard(`milestone-assessment:${milestone.milestone_key}`, evidenceNote !== "");
  const achievementLabel = MILESTONE_ACHIEVEMENT_LABEL[milestone.achievement];

  // §4.3: the server refuses any assessment while the definition is not
  // `confirmed` (422 `product_milestone_not_assessable`). That is the
  // server's own documented gate on a server value, so the buttons are not
  // offered before it -- the reason is, instead.
  if (milestone.design_status !== "confirmed") {
    return (
      <div className="space-y-1 rounded border p-2" data-testid="milestone-assessment-controls">
        <p className="text-xs font-semibold">Milestone の達成を判定する</p>
        <p className="text-xs text-muted-foreground" data-testid="milestone-assessment-not-available">
          定義がまだ確定していないため、達成は判定できません(定義の状態: {DESIGN_STATUS_LABEL[milestone.design_status]})。
          {milestone.design_status === "proposed"
            ? "先に上の「確定する」で定義を確定してください。"
            : "却下・廃止された Milestone は判定の対象になりません。"}
        </p>
      </div>
    );
  }

  function submit(assessment: ProductMilestoneAssessmentKind, rationale: string) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { assessment, rationale, evidence_note: evidenceNote, captured_digest: capturedDigest },
      {
        onSuccess: () => {
          dialog.close();
          setEvidenceNote("");
          toast.success(`「${MILESTONE_ASSESSMENT_ACTION_LABEL[assessment]}」を記録しました`);
        },
        onError: (error) => {
          dialog.close();
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした"));
        },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="milestone-assessment-controls">
      <p className="text-xs font-semibold">Milestone の達成を判定する(人間の判断として記録されます)</p>
      <p className="text-xs" data-testid="milestone-assessment-current-state">
        現在の判定: {achievementLabel}({MILESTONE_ASSESSABILITY_LABEL[milestone.assessability]})
      </p>
      <Textarea aria-label="根拠" placeholder="根拠(任意)" value={evidenceNote} onChange={(e) => setEvidenceNote(e.target.value)} rows={2} />
      <div className="flex flex-wrap gap-2">
        {MILESTONE_ASSESSMENT_VALUES.map((a) => (
          <Button
            key={a} size="sm" variant="outline" disabled={record.isPending}
            onClick={() => dialog.open(a)} data-testid={`milestone-assessment-${a}`}
          >
            {MILESTONE_ASSESSMENT_ACTION_LABEL[a]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} />}
      {dialog.pendingKind && (
        <DecisionConfirmDialog
          open
          onOpenChange={(open) => { if (!open) dialog.close(); }}
          title={`Milestone「${milestone.title || milestone.milestone_key}」を${MILESTONE_ASSESSMENT_ACTION_LABEL[dialog.pendingKind]}`}
          currentStateText={`現在の判定: ${achievementLabel}`}
          consequence={`${HUMAN_DECISION_NOTE} 達成の判定は Objective を自動で達成にしません。`}
          confirmLabel={MILESTONE_ASSESSMENT_ACTION_LABEL[dialog.pendingKind]}
          reasonLabel="判定理由"
          pending={record.isPending}
          onConfirm={(reason) => submit(dialog.pendingKind!, reason)}
        />
      )}
    </div>
  );
}

/** The selected Milestone's work surface: content revision, definition
 * decision, and achievement assessment, all scoped to the ONE Milestone this
 * panel was given. §1.3: definition confirmation and achievement judgement
 * are two separate controls, never merged into one action. */
export function MilestoneWorkPanel({ milestoneKey }: { milestoneKey: string }) {
  const detail = useProductMilestoneDetail(milestoneKey);

  if (detail.isLoading) {
    return <p className="text-sm text-muted-foreground" data-testid="milestone-work-panel-loading">読み込んでいます…</p>;
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm" role="alert" data-testid="milestone-work-panel-error">
        <p className="font-medium text-destructive">Milestone の詳細を取得できませんでした。</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => detail.refetch()}>再試行</Button>
      </div>
    );
  }

  const milestone = detail.data;
  const digest = milestone.current_revision?.content_digest ?? "";

  return (
    <div className="space-y-3" data-testid={`milestone-work-panel-${milestone.milestone_key}`}>
      <MilestoneRevisionForm
        milestoneKey={milestone.milestone_key}
        current={milestone.current_revision}
      />
      <MilestoneDecisionControls milestone={milestone} capturedDigest={digest} onStale={() => detail.refetch()} />
      <MilestoneAssessmentControls milestone={milestone} capturedDigest={digest} onStale={() => detail.refetch()} />
    </div>
  );
}
