// Issue #427 (Epic #427) §9.3/D: the Objective Map's own forms for the
// finite next-step operations THIS Epic owns -- Objective create / revision
// / decision, and Milestone create / revision / decision / assessment.
// `confirm_vision` and `link_requirement_to_feature` are deliberately NOT
// here; those next steps deep-link to the screens that already own that
// surface (Interview / UX Design Studio) rather than duplicating it.
//
// The CTA that leads here NAVIGATES (§9.3) -- every write below happens only
// on explicit developer submission, exactly like the Gap Workbench's
// existing decision/artifact-link controls (§9.2 non-goal: no automatic
// execution on selection). Every decision/assessment sends `captured_digest`
// read from the entity's OWN response (`current_revision.content_digest`,
// §4.2/§10.1) -- never a client-computed value -- and a stale-digest 409
// renders `StaleDigestNotice`: a recoverable message asking the developer to
// reload and re-read before deciding again, never a silent or automatic
// retry (§B).

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/api/client";
import {
  useAddProductMilestoneRevision, useAddProductObjectiveRevision,
  useCreateProductMilestone, useCreateProductObjective,
  useProductMilestoneDetail, useProductObjectiveDetail,
  useRecordProductMilestoneAssessment, useRecordProductMilestoneDecision,
  useRecordProductObjectiveDecision,
} from "@/api/hooks";
import type {
  ProductMilestoneAssessmentKind, ProductMilestoneDecisionKind,
  ProductMilestoneVerificationMethod, ProductObjectiveDecisionKind,
} from "@/api/types";
import {
  MILESTONE_ASSESSMENT_ACTION_LABEL, MILESTONE_DECISION_LABEL,
  OBJECTIVE_DECISION_LABEL, isStaleDigestErrorCode,
} from "./model";

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

type Rejection = { code: string; message: string };

function rejectionFromError(error: unknown, fallback: string): Rejection {
  const apiError = error as ApiError;
  return { code: apiError.code ?? "unknown", message: apiError.detail || fallback };
}

function RejectedNotice({ code, message }: Rejection) {
  return (
    <div className="rounded border border-destructive p-2 text-xs" data-testid="product-decision-rejected">
      <span className="font-mono text-destructive">{code}</span>
      <span className="ml-2">{message}</span>
    </div>
  );
}

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

function ObjectiveRevisionForm({ objectiveKey }: { objectiveKey: string }) {
  const add = useAddProductObjectiveRevision(objectiveKey);
  const [title, setTitle] = useState("");
  const [intent, setIntent] = useState("");
  const [contribution, setContribution] = useState("");
  const [summary, setSummary] = useState("");

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

function ObjectiveDecisionControls({
  objectiveKey, capturedDigest, onStale,
}: {
  objectiveKey: string;
  capturedDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductObjectiveDecision(objectiveKey);
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);

  function submit(decision: ProductObjectiveDecisionKind) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { decision, rationale, captured_digest: capturedDigest },
      {
        onSuccess: () => { setRationale(""); toast.success(`「${OBJECTIVE_DECISION_LABEL[decision]}」を記録しました`); },
        onError: (error) => {
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした"));
        },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="objective-decision-controls">
      <p className="text-xs font-semibold">解消状態を変える(人間の判断として記録されます)</p>
      <Textarea aria-label="理由" placeholder="理由(任意)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <div className="flex flex-wrap gap-2">
        {OBJECTIVE_DECISION_VALUES.map((d) => (
          <Button
            key={d} size="sm" variant="outline" disabled={record.isPending}
            onClick={() => submit(d)} data-testid={`objective-decision-${d}`}
          >
            {OBJECTIVE_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} />}
    </div>
  );
}

/** The selected Objective's work surface: content revision + decision
 * controls, both scoped to the ONE Objective this panel was given. Fetches
 * `GET /product-objectives/{key}` directly (§4.2's `current_revision.
 * content_digest`), separate from the Objective Map tree's own summary
 * fetch. */
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
      <ObjectiveRevisionForm objectiveKey={objective.objective_key} />
      <ObjectiveDecisionControls objectiveKey={objective.objective_key} capturedDigest={digest} onStale={() => detail.refetch()} />
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

function MilestoneRevisionForm({ milestoneKey }: { milestoneKey: string }) {
  const add = useAddProductMilestoneRevision(milestoneKey);
  const [title, setTitle] = useState("");
  const [targetState, setTargetState] = useState("");
  const [verificationMethod, setVerificationMethod] = useState<ProductMilestoneVerificationMethod>("manual_review");
  const [verificationNote, setVerificationNote] = useState("");
  const [sequenceHint, setSequenceHint] = useState("0");
  const [summary, setSummary] = useState("");

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
  milestoneKey, capturedDigest, onStale,
}: {
  milestoneKey: string;
  capturedDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductMilestoneDecision(milestoneKey);
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);

  function submit(decision: ProductMilestoneDecisionKind) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { decision, rationale, captured_digest: capturedDigest },
      {
        onSuccess: () => { setRationale(""); toast.success(`「${MILESTONE_DECISION_LABEL[decision]}」を記録しました`); },
        onError: (error) => {
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした"));
        },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="milestone-decision-controls">
      <p className="text-xs font-semibold">Milestone の定義を確定する(人間の判断として記録されます)</p>
      <Textarea aria-label="理由" placeholder="理由(任意)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <div className="flex flex-wrap gap-2">
        {MILESTONE_DECISION_VALUES.map((d) => (
          <Button
            key={d} size="sm" variant="outline" disabled={record.isPending}
            onClick={() => submit(d)} data-testid={`milestone-decision-${d}`}
          >
            {MILESTONE_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} />}
    </div>
  );
}

function MilestoneAssessmentControls({
  milestoneKey, capturedDigest, onStale,
}: {
  milestoneKey: string;
  capturedDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductMilestoneAssessment(milestoneKey);
  const [rationale, setRationale] = useState("");
  const [evidenceNote, setEvidenceNote] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);

  function submit(assessment: ProductMilestoneAssessmentKind) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { assessment, rationale, evidence_note: evidenceNote, captured_digest: capturedDigest },
      {
        onSuccess: () => {
          setRationale(""); setEvidenceNote("");
          toast.success(`「${MILESTONE_ASSESSMENT_ACTION_LABEL[assessment]}」を記録しました`);
        },
        onError: (error) => {
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
      <Textarea aria-label="判定理由" placeholder="判定理由(任意)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <Textarea aria-label="根拠" placeholder="根拠(任意)" value={evidenceNote} onChange={(e) => setEvidenceNote(e.target.value)} rows={2} />
      <div className="flex flex-wrap gap-2">
        {MILESTONE_ASSESSMENT_VALUES.map((a) => (
          <Button
            key={a} size="sm" variant="outline" disabled={record.isPending}
            onClick={() => submit(a)} data-testid={`milestone-assessment-${a}`}
          >
            {MILESTONE_ASSESSMENT_ACTION_LABEL[a]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} />}
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
      <MilestoneRevisionForm milestoneKey={milestone.milestone_key} />
      <MilestoneDecisionControls milestoneKey={milestone.milestone_key} capturedDigest={digest} onStale={() => detail.refetch()} />
      <MilestoneAssessmentControls milestoneKey={milestone.milestone_key} capturedDigest={digest} onStale={() => detail.refetch()} />
    </div>
  );
}
