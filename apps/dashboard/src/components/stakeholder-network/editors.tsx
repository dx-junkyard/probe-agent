// UX audit 2026-11-17 C1: the editing surface of the Stakeholder Value
// Network screen -- create / revise / decide Stakeholder, Need, Environment
// Observation and Value Exchange (`docs/01-specifications/product/
// stakeholder-value-network.md` §10).
//
// What these forms deliberately never do:
// * send `created_by` / `decided_by` / `decision_method` / `authored_by_kind`
//   -- the server takes all four from the route and the Principal (§10);
// * set or recompute `design_status` / `recheck_state` -- the only write
//   that moves `design_status` is an explicit decision the developer
//   confirms in a dialog (invariant 9: AI proposes, only a human decides);
// * ask for a Stakeholder key by hand where one already exists -- provider /
//   receiver / impact targets are chosen from existing rows;
// * sum or compute any amount -- money is described, never processed (§11).
//
// Every form is remounted per entity by its caller (`key=`), so text typed
// for one Stakeholder can never be saved as another's revision (#427's
// lesson), and revision forms are seeded from the CURRENT revision: the
// revision API appends a full snapshot, so starting blank would turn "fix one
// field" into "blank every other field".

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { getSystemId } from "@/api/client";
import {
  useAddStakeholderNeedRevision, useAddStakeholderRevision, useAddValueExchangeRevision,
  useCreateEnvironmentObservation, useCreateStakeholder, useCreateStakeholderNeed,
  useCreateValueExchange, useEnvironmentObservationDetails, useEnvironmentObservations,
  useRecordStakeholderDecision, useStakeholderDetail, useStakeholderNeedDetail, useStakeholderNeeds,
  useValueExchangeDetail,
} from "@/api/hooks";
import type {
  EnvironmentImpactKind, EnvironmentObservationConfidence, EnvironmentObservationDetailOut,
  StakeholderDecisionKind, StakeholderDecisionSubjectKind, StakeholderNeedKind, StakeholderNeedOut,
  StakeholderRevisionOut, ValueExchangeRevisionOut, ValueNetworkCadence, ValueNetworkConsiderationState,
  ValueNetworkDesignStatus, ValueNetworkExchangeKind, ValueNetworkNodeOut, ValueNetworkRecheckState,
  ValueNetworkStakeholderKind,
} from "@/api/types";
import { QueryErrorState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Dialog, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useUnsavedChangesGuard } from "@/lib/ui-draft";
import {
  AUTHORSHIP_KIND_LABEL, CADENCE_LABEL, CONSIDERATION_STATE_LABEL, DESIGN_STATUS_LABEL,
  EXCHANGE_KIND_LABEL, RECHECK_STATE_LABEL, STAKEHOLDER_KIND_LABEL,
} from "./model";
import {
  DECISION_DESCRIPTION, DECISION_LABEL, IMPACT_KIND_LABEL, IMPACT_KIND_VALUES, NEED_KIND_LABEL,
  NEED_KIND_VALUES, OBSERVATION_CONFIDENCE_LABEL, OBSERVATION_CONFIDENCE_VALUES, allowedDecisions,
  describeWriteFailure, normalizeKey,
} from "./editing";
import { EmptyNote, StateBadge } from "./shared";

const STAKEHOLDER_KIND_VALUES: ValueNetworkStakeholderKind[] = [
  "end_user", "customer_organization", "internal_operator", "provider_team", "partner", "regulator", "other",
];
const EXCHANGE_KIND_VALUES: ValueNetworkExchangeKind[] = [
  "experience", "service", "information", "money", "authority", "obligation", "risk",
];
const CONSIDERATION_STATE_VALUES: ValueNetworkConsiderationState[] = ["unknown", "present", "none"];
const CADENCE_VALUES: ValueNetworkCadence[] = ["unknown", "one_time", "recurring", "continuous", "on_demand"];

function guardId(...parts: string[]): string {
  return ["stakeholder-network", String(getSystemId() ?? ""), ...parts].join(":");
}

/** A rejected write, next to the form that sent it. The finite code is a
 * supplement, never the headline. */
export function WriteError({ error, testId }: { error: unknown; testId?: string }) {
  if (!error) return null;
  const failure = describeWriteFailure(error);
  return (
    <div role="alert" className="rounded border border-destructive/40 bg-destructive/5 p-2 text-xs" data-testid={testId ?? "value-network-write-error"}>
      <p className="text-destructive">{failure.message}</p>
      {failure.code && <p className="mt-0.5 text-muted-foreground">コード: {failure.code}</p>}
    </div>
  );
}

function FormShell({
  title,
  testId,
  onSubmit,
  pending,
  submitLabel,
  canSubmit,
  error,
  children,
}: {
  title?: string;
  testId: string;
  onSubmit: () => void;
  pending: boolean;
  submitLabel: string;
  canSubmit: boolean;
  error: unknown;
  children: ReactNode;
}) {
  function handle(e: FormEvent) {
    e.preventDefault();
    if (!pending && canSubmit) onSubmit();
  }
  return (
    <form onSubmit={handle} className="space-y-3" data-testid={testId} noValidate>
      {title && <h4 className="text-sm font-semibold">{title}</h4>}
      <fieldset disabled={pending} className="space-y-3">
        {children}
      </fieldset>
      <WriteError error={error} testId={`${testId}-error`} />
      <Button type="submit" size="sm" disabled={pending || !canSubmit}>
        {pending ? "保存しています…" : submitLabel}
      </Button>
    </form>
  );
}

// --- Decisions ---------------------------------------------------------------

export function DecisionControls({
  subjectKind,
  subjectKey,
  designStatus,
  recheckState,
  capturedDigest,
  digestReady,
  testId,
}: {
  subjectKind: StakeholderDecisionSubjectKind;
  subjectKey: string;
  designStatus: ValueNetworkDesignStatus;
  recheckState: ValueNetworkRecheckState;
  /** The content digest the developer is looking at. The server refuses the
   * decision when it moved (409 `stakeholder_decision_stale_digest`). */
  capturedDigest: string;
  digestReady: boolean;
  testId: string;
}) {
  const menu = allowedDecisions(designStatus, recheckState);
  const [pendingDecision, setPendingDecision] = useState<StakeholderDecisionKind | null>(null);
  const [rationale, setRationale] = useState("");
  const record = useRecordStakeholderDecision();
  useUnsavedChangesGuard(guardId("decision", subjectKind, subjectKey), pendingDecision !== null && rationale.trim() !== "");

  function open(decision: StakeholderDecisionKind) {
    record.reset();
    setRationale("");
    setPendingDecision(decision);
  }

  function submit() {
    if (!pendingDecision) return;
    record.mutate(
      {
        subject_kind: subjectKind, subject_key: subjectKey, decision: pendingDecision,
        rationale: rationale.trim(), captured_digest: capturedDigest,
      },
      { onSuccess: () => { setPendingDecision(null); setRationale(""); } },
    );
  }

  const destructive = pendingDecision === "reject" || pendingDecision === "retire";
  return (
    <div className="space-y-2" data-testid={testId}>
      <div className="flex flex-wrap gap-2">
        {menu.primary && (
          <Button size="sm" disabled={!digestReady} onClick={() => open(menu.primary!)} data-testid={`${testId}-${menu.primary}`}>
            {menu.primary === "confirm" && designStatus === "confirmed" ? "再確認して確定する" : DECISION_LABEL[menu.primary]}
          </Button>
        )}
        {menu.secondary.map((d) => (
          <Button key={d} size="sm" variant="outline" disabled={!digestReady} onClick={() => open(d)} data-testid={`${testId}-${d}`}>
            {DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {!pendingDecision && record.isError && <WriteError error={record.error} testId={`${testId}-error`} />}
      <Dialog open={pendingDecision !== null} onOpenChange={(o) => { if (!o) setPendingDecision(null); }} closeOnOverlayClick={false}>
        {pendingDecision && (
          <div data-testid={`${testId}-dialog`}>
            <DialogHeader>
              <DialogTitle>{DECISION_LABEL[pendingDecision]}</DialogTitle>
              <DialogDescription>{DECISION_DESCRIPTION[pendingDecision]}</DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">
                対象: <span className="font-mono">{subjectKey}</span>(現在: {DESIGN_STATUS_LABEL[designStatus]})。
                判断は手動の記録として、ログイン中の利用者の名前で残ります。
              </p>
              <FormField label="理由(任意)">
                {(field) => (
                  <Textarea {...field} value={rationale} onChange={(e) => setRationale(e.target.value)} rows={3} />
                )}
              </FormField>
              <WriteError error={record.error} testId={`${testId}-dialog-error`} />
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setPendingDecision(null)} {...(destructive ? { "data-autofocus": "" } : {})}>
                  取り消す
                </Button>
                <Button
                  size="sm"
                  variant={destructive ? "destructive" : "default"}
                  disabled={record.isPending}
                  onClick={submit}
                  data-testid={`${testId}-submit`}
                >
                  {record.isPending ? "記録しています…" : DECISION_LABEL[pendingDecision]}
                </Button>
              </div>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}

// --- Stakeholder ---------------------------------------------------------------

interface StakeholderFields {
  display_name: string;
  stakeholder_kind: ValueNetworkStakeholderKind;
  description: string;
  context_note: string;
}

function stakeholderFieldsDirty(a: StakeholderFields, b: StakeholderFields): boolean {
  return a.display_name.trim() !== b.display_name.trim()
    || a.stakeholder_kind !== b.stakeholder_kind
    || a.description.trim() !== b.description.trim()
    || a.context_note.trim() !== b.context_note.trim();
}

function StakeholderFieldInputs({
  value,
  onChange,
}: {
  value: StakeholderFields;
  onChange: (next: StakeholderFields) => void;
}) {
  return (
    <>
      <FormField label="表示名">
        {(field) => (
          <Input {...field} value={value.display_name} onChange={(e) => onChange({ ...value, display_name: e.target.value })} />
        )}
      </FormField>
      <FormField label="種類">
        {(field) => (
          <Select
            {...field}
            value={value.stakeholder_kind}
            onChange={(e) => onChange({ ...value, stakeholder_kind: e.target.value as ValueNetworkStakeholderKind })}
          >
            {STAKEHOLDER_KIND_VALUES.map((k) => (
              <option key={k} value={k}>{STAKEHOLDER_KIND_LABEL[k]}</option>
            ))}
          </Select>
        )}
      </FormField>
      <FormField label="説明">
        {(field) => (
          <Textarea {...field} rows={2} value={value.description} onChange={(e) => onChange({ ...value, description: e.target.value })} />
        )}
      </FormField>
      <FormField label="置かれている状況">
        {(field) => (
          <Textarea {...field} rows={2} value={value.context_note} onChange={(e) => onChange({ ...value, context_note: e.target.value })} />
        )}
      </FormField>
    </>
  );
}

const EMPTY_STAKEHOLDER: StakeholderFields = { display_name: "", stakeholder_kind: "other", description: "", context_note: "" };

export function StakeholderCreateForm({ onCreated }: { onCreated: (stakeholderKey: string) => void }) {
  const [key, setKey] = useState("");
  const [fields, setFields] = useState<StakeholderFields>(EMPTY_STAKEHOLDER);
  const create = useCreateStakeholder();
  const dirty = key.trim() !== "" || stakeholderFieldsDirty(fields, EMPTY_STAKEHOLDER);
  useUnsavedChangesGuard(guardId("stakeholder-create"), dirty);

  function submit() {
    const stakeholderKey = normalizeKey(key);
    create.mutate(
      {
        stakeholder_key: stakeholderKey, display_name: fields.display_name.trim(),
        stakeholder_kind: fields.stakeholder_kind, description: fields.description.trim(),
        context_note: fields.context_note.trim(),
      },
      {
        onSuccess: (out) => {
          setKey("");
          setFields(EMPTY_STAKEHOLDER);
          onCreated(out.stakeholder_key);
        },
      },
    );
  }

  return (
    <FormShell
      testId="stakeholder-create-form"
      onSubmit={submit}
      pending={create.isPending}
      submitLabel="Stakeholder を追加"
      canSubmit={key.trim() !== ""}
      error={create.error}
    >
      <FormField label="key" required hint="この System の中で一意な識別子です(例: end-user)。後から変更できません。">
        {(field) => <Input {...field} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />}
      </FormField>
      <StakeholderFieldInputs value={fields} onChange={setFields} />
    </FormShell>
  );
}

function StakeholderRevisionFormInner({ stakeholderKey, revision }: { stakeholderKey: string; revision: StakeholderRevisionOut | null }) {
  const seed: StakeholderFields = revision
    ? {
      display_name: revision.display_name, stakeholder_kind: revision.stakeholder_kind,
      description: revision.description, context_note: revision.context_note,
    }
    : EMPTY_STAKEHOLDER;
  const [fields, setFields] = useState<StakeholderFields>(seed);
  const [changeNote, setChangeNote] = useState("");
  const save = useAddStakeholderRevision();
  const dirty = stakeholderFieldsDirty(fields, seed) || changeNote.trim() !== "";
  useUnsavedChangesGuard(guardId("stakeholder-revision", stakeholderKey), dirty);

  return (
    <FormShell
      title="内容を編集"
      testId="stakeholder-revision-form"
      onSubmit={() => save.mutate({
        stakeholderKey,
        body: {
          display_name: fields.display_name.trim(), stakeholder_kind: fields.stakeholder_kind,
          description: fields.description.trim(), context_note: fields.context_note.trim(),
          change_note: changeNote.trim(),
        },
      })}
      pending={save.isPending}
      submitLabel="新しい版として保存"
      canSubmit={dirty}
      error={save.error}
    >
      <StakeholderFieldInputs value={fields} onChange={setFields} />
      <FormField label="変更の理由(任意)">
        {(field) => <Input {...field} value={changeNote} onChange={(e) => setChangeNote(e.target.value)} />}
      </FormField>
    </FormShell>
  );
}

/** The Stakeholder's own revision form + decisions, read from the entity's
 * detail (the projection does not carry the revision text or its digest). */
export function StakeholderEditor({ stakeholderKey }: { stakeholderKey: string }) {
  const detail = useStakeholderDetail(stakeholderKey);
  if (detail.isLoading) return <p className="text-xs text-muted-foreground">内容を読み込んでいます…</p>;
  if (detail.isError || !detail.data) {
    return <QueryErrorState target="Stakeholder の内容" error={detail.error} onRetry={() => { void detail.refetch(); }} retrying={detail.isFetching} data-testid="stakeholder-detail-error" />;
  }
  const d = detail.data;
  return (
    <div className="space-y-3" data-testid="stakeholder-editor">
      <DecisionControls
        subjectKind="stakeholder"
        subjectKey={stakeholderKey}
        designStatus={d.design_status}
        recheckState={d.recheck_state}
        capturedDigest={d.current_revision?.content_digest ?? ""}
        digestReady
        testId="stakeholder-decision"
      />
      {d.current_revision && (
        <p className="text-xs text-muted-foreground">
          版 {d.current_revision.revision_number}・{AUTHORSHIP_KIND_LABEL[d.current_revision.authored_by_kind]}
        </p>
      )}
      <StakeholderRevisionFormInner
        key={`${stakeholderKey}:${d.current_revision_id ?? "none"}`}
        stakeholderKey={stakeholderKey}
        revision={d.current_revision}
      />
    </div>
  );
}

// --- Needs ----------------------------------------------------------------------

interface NeedFields {
  need_kind: StakeholderNeedKind;
  statement: string;
  rationale: string;
}

const EMPTY_NEED: NeedFields = { need_kind: "unmet_need", statement: "", rationale: "" };

function needDirty(a: NeedFields, b: NeedFields): boolean {
  return a.need_kind !== b.need_kind || a.statement.trim() !== b.statement.trim() || a.rationale.trim() !== b.rationale.trim();
}

function NeedFieldInputs({ value, onChange }: { value: NeedFields; onChange: (next: NeedFields) => void }) {
  return (
    <>
      <FormField label="種類">
        {(field) => (
          <Select {...field} value={value.need_kind} onChange={(e) => onChange({ ...value, need_kind: e.target.value as StakeholderNeedKind })}>
            {NEED_KIND_VALUES.map((k) => <option key={k} value={k}>{NEED_KIND_LABEL[k]}</option>)}
          </Select>
        )}
      </FormField>
      <FormField label="内容">
        {(field) => <Textarea {...field} rows={2} value={value.statement} onChange={(e) => onChange({ ...value, statement: e.target.value })} />}
      </FormField>
      <FormField label="根拠・背景(任意)">
        {(field) => <Textarea {...field} rows={2} value={value.rationale} onChange={(e) => onChange({ ...value, rationale: e.target.value })} />}
      </FormField>
    </>
  );
}

function NeedCreateForm({ stakeholderKey }: { stakeholderKey: string }) {
  const [key, setKey] = useState("");
  const [fields, setFields] = useState<NeedFields>(EMPTY_NEED);
  const create = useCreateStakeholderNeed();
  const dirty = key.trim() !== "" || needDirty(fields, EMPTY_NEED);
  useUnsavedChangesGuard(guardId("need-create", stakeholderKey), dirty);
  return (
    <FormShell
      title="Need を追加"
      testId="need-create-form"
      onSubmit={() => create.mutate(
        {
          need_key: normalizeKey(key), stakeholder_key: stakeholderKey, need_kind: fields.need_kind,
          statement: fields.statement.trim(), rationale: fields.rationale.trim(),
        },
        { onSuccess: () => { setKey(""); setFields(EMPTY_NEED); } },
      )}
      pending={create.isPending}
      submitLabel="Need を追加"
      canSubmit={key.trim() !== ""}
      error={create.error}
    >
      <FormField label="Need の key" required hint="この System の中で一意な識別子です。">
        {(field) => <Input {...field} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />}
      </FormField>
      <NeedFieldInputs value={fields} onChange={setFields} />
    </FormShell>
  );
}

function NeedItem({ need }: { need: StakeholderNeedOut }) {
  const [editing, setEditing] = useState(false);
  return (
    <li className="space-y-2 rounded border p-2" data-testid={`need-item-${need.need_key}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <StateBadge label={NEED_KIND_LABEL[need.need_kind]} tone="outline" />
        <StateBadge label={DESIGN_STATUS_LABEL[need.design_status]} tone={need.design_status === "confirmed" ? "success" : "outline"} />
        {need.recheck_state === "stale" && <StateBadge label={RECHECK_STATE_LABEL.stale} tone="warning" />}
        <span className="font-mono text-xs text-muted-foreground">{need.need_key}</span>
      </div>
      <p className="text-sm">{need.statement || "(内容なし)"}</p>
      <NeedDecisionAndRevision need={need} editing={editing} onToggle={() => setEditing((v) => !v)} />
    </li>
  );
}

function NeedDecisionAndRevision({ need, editing, onToggle }: { need: StakeholderNeedOut; editing: boolean; onToggle: () => void }) {
  return (
    <div className="space-y-2">
      <NeedDetailControls need={need} editing={editing} />
      <Button variant="ghost" size="sm" onClick={onToggle} aria-expanded={editing}>
        {editing ? "編集を閉じる" : "内容を編集"}
      </Button>
    </div>
  );
}

function NeedDetailControls({ need, editing }: { need: StakeholderNeedOut; editing: boolean }) {
  // The list row carries no digest; the decision captures the current
  // revision's digest from the detail read.
  const detail = useStakeholderNeedDetail(need.need_key);
  const revision = detail.data?.current_revision ?? null;
  return (
    <>
      <DecisionControls
        subjectKind="stakeholder_need"
        subjectKey={need.need_key}
        designStatus={need.design_status}
        recheckState={need.recheck_state}
        capturedDigest={revision?.content_digest ?? ""}
        digestReady={!!detail.data}
        testId={`need-decision-${need.need_key}`}
      />
      {editing && detail.data && (
        <NeedRevisionForm
          key={`${need.need_key}:${detail.data.current_revision_id ?? "none"}`}
          needKey={need.need_key}
          stakeholderKey={need.stakeholder_key}
          seed={revision ? { need_kind: revision.need_kind, statement: revision.statement, rationale: revision.rationale } : EMPTY_NEED}
        />
      )}
    </>
  );
}

function NeedRevisionForm({ needKey, stakeholderKey, seed }: { needKey: string; stakeholderKey: string; seed: NeedFields }) {
  const [fields, setFields] = useState<NeedFields>(seed);
  const [changeNote, setChangeNote] = useState("");
  const save = useAddStakeholderNeedRevision();
  const dirty = needDirty(fields, seed) || changeNote.trim() !== "";
  useUnsavedChangesGuard(guardId("need-revision", needKey), dirty);
  return (
    <FormShell
      testId={`need-revision-form-${needKey}`}
      onSubmit={() => save.mutate({
        needKey,
        body: {
          need_kind: fields.need_kind, statement: fields.statement.trim(), rationale: fields.rationale.trim(),
          stakeholder_key: stakeholderKey, change_note: changeNote.trim(),
        },
      })}
      pending={save.isPending}
      submitLabel="新しい版として保存"
      canSubmit={dirty}
      error={save.error}
    >
      <NeedFieldInputs value={fields} onChange={setFields} />
      <FormField label="変更の理由(任意)">
        {(field) => <Input {...field} value={changeNote} onChange={(e) => setChangeNote(e.target.value)} />}
      </FormField>
    </FormShell>
  );
}

export function NeedSection({ stakeholderKey }: { stakeholderKey: string }) {
  const needs = useStakeholderNeeds();
  const mine = (needs.data?.needs ?? []).filter((n) => n.stakeholder_key === stakeholderKey);
  return (
    <section className="space-y-2" data-testid="stakeholder-needs">
      <h4 className="text-sm font-semibold">Need(要望・困りごと・制約・期待)</h4>
      {needs.isLoading ? (
        <p className="text-xs text-muted-foreground">読み込んでいます…</p>
      ) : needs.isError && !needs.data ? (
        <QueryErrorState target="Need 一覧" error={needs.error} onRetry={() => { void needs.refetch(); }} retrying={needs.isFetching} data-testid="stakeholder-needs-error" />
      ) : mine.length === 0 ? (
        <EmptyNote>この Stakeholder の Need はまだありません。下のフォームから追加できます。</EmptyNote>
      ) : (
        <ul className="space-y-2">{mine.map((n) => <NeedItem key={n.need_key} need={n} />)}</ul>
      )}
      <details className="rounded border p-2">
        <summary className="cursor-pointer text-sm">Need を追加</summary>
        <div className="mt-2"><NeedCreateForm stakeholderKey={stakeholderKey} /></div>
      </details>
    </section>
  );
}

// --- Environment Observations ---------------------------------------------------

/** An observation "belongs" to this Stakeholder's pane when one of its
 * impacts points at the Stakeholder or one of its Needs -- an exact key
 * match on the server-stored impact target, nothing inferred. */
function observationTouches(o: EnvironmentObservationDetailOut, stakeholderKey: string, needKeys: Set<string>): boolean {
  return o.impacts.some((i) =>
    (i.target_ref_kind === "stakeholder" && i.target_ref === stakeholderKey)
    || (i.target_ref_kind === "stakeholder_need" && needKeys.has(i.target_ref)));
}

function ObservationCreateForm({
  stakeholderKey,
  needs,
  supersedes,
  onDone,
}: {
  stakeholderKey: string;
  needs: StakeholderNeedOut[];
  supersedes?: EnvironmentObservationDetailOut;
  onDone?: () => void;
}) {
  const initialTarget = supersedes?.impacts[0]
    ? `${supersedes.impacts[0].target_ref_kind}|${supersedes.impacts[0].target_ref}`
    : `stakeholder|${stakeholderKey}`;
  const [key, setKey] = useState("");
  const [statement, setStatement] = useState(supersedes?.statement ?? "");
  const [sourceNote, setSourceNote] = useState(supersedes?.source_note ?? "");
  const [confidence, setConfidence] = useState<EnvironmentObservationConfidence>(supersedes?.observation_confidence ?? "reported");
  const [impactKind, setImpactKind] = useState<EnvironmentImpactKind>(supersedes?.impacts[0]?.impact_kind ?? "creates");
  const [target, setTarget] = useState(initialTarget);
  const create = useCreateEnvironmentObservation();
  const dirty = key.trim() !== "" || statement.trim() !== (supersedes?.statement ?? "").trim()
    || sourceNote.trim() !== (supersedes?.source_note ?? "").trim();
  useUnsavedChangesGuard(guardId("observation-create", stakeholderKey, supersedes?.observation_key ?? ""), dirty);

  function submit() {
    const [targetKind, targetRef] = target.split("|", 2) as ["stakeholder" | "stakeholder_need", string];
    create.mutate(
      {
        observation_key: normalizeKey(key), statement: statement.trim(), source_note: sourceNote.trim(),
        observation_confidence: confidence,
        supersedes_observation_key: supersedes?.observation_key ?? null,
        impacts: [{ impact_kind: impactKind, target_ref_kind: targetKind, target_ref: targetRef, note: "" }],
      },
      {
        onSuccess: () => {
          setKey(""); setStatement(""); setSourceNote("");
          onDone?.();
        },
      },
    );
  }

  return (
    <FormShell
      title={supersedes ? `${supersedes.observation_key} を訂正する新しい観測` : "環境の観測を追加"}
      testId={supersedes ? `observation-supersede-form-${supersedes.observation_key}` : "observation-create-form"}
      onSubmit={submit}
      pending={create.isPending}
      submitLabel={supersedes ? "訂正として記録" : "観測を追加"}
      canSubmit={key.trim() !== "" && statement.trim() !== ""}
      error={create.error}
    >
      {supersedes && (
        <p className="text-xs text-muted-foreground">
          観測は書き換えず、元の観測を置き換える新しい観測として記録します。元の観測も履歴として残ります。
        </p>
      )}
      <FormField label="観測の key" required hint="この System の中で一意な識別子です。">
        {(field) => <Input {...field} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />}
      </FormField>
      <FormField label="観測した内容" required>
        {(field) => <Textarea {...field} rows={2} value={statement} onChange={(e) => setStatement(e.target.value)} />}
      </FormField>
      <FormField label="どうやって知ったか">
        {(field) => (
          <Select {...field} value={confidence} onChange={(e) => setConfidence(e.target.value as EnvironmentObservationConfidence)}>
            {OBSERVATION_CONFIDENCE_VALUES.map((c) => <option key={c} value={c}>{OBSERVATION_CONFIDENCE_LABEL[c]}</option>)}
          </Select>
        )}
      </FormField>
      <FormField label="出典・メモ(任意)">
        {(field) => <Input {...field} value={sourceNote} onChange={(e) => setSourceNote(e.target.value)} />}
      </FormField>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="影響">
          {(field) => (
            <Select {...field} value={impactKind} onChange={(e) => setImpactKind(e.target.value as EnvironmentImpactKind)}>
              {IMPACT_KIND_VALUES.map((k) => <option key={k} value={k}>{IMPACT_KIND_LABEL[k]}</option>)}
            </Select>
          )}
        </FormField>
        <FormField label="影響先">
          {(field) => (
            <Select {...field} value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value={`stakeholder|${stakeholderKey}`}>この Stakeholder</option>
              {needs.map((n) => (
                <option key={n.need_key} value={`stakeholder_need|${n.need_key}`}>
                  Need: {n.statement || n.need_key}
                </option>
              ))}
            </Select>
          )}
        </FormField>
      </div>
    </FormShell>
  );
}

function ObservationItem({
  observation,
  stakeholderKey,
  needs,
}: {
  observation: EnvironmentObservationDetailOut;
  stakeholderKey: string;
  needs: StakeholderNeedOut[];
}) {
  const [correcting, setCorrecting] = useState(false);
  return (
    <li className="space-y-2 rounded border p-2" data-testid={`observation-item-${observation.observation_key}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <StateBadge label={OBSERVATION_CONFIDENCE_LABEL[observation.observation_confidence]} tone="outline" />
        <StateBadge label={DESIGN_STATUS_LABEL[observation.design_status]} tone={observation.design_status === "confirmed" ? "success" : "outline"} />
        {observation.recheck_state === "stale" && <StateBadge label={RECHECK_STATE_LABEL.stale} tone="warning" />}
        <span className="font-mono text-xs text-muted-foreground">{observation.observation_key}</span>
      </div>
      <p className="text-sm">{observation.statement}</p>
      <ul className="text-xs text-muted-foreground">
        {observation.impacts.map((i) => (
          <li key={i.id}>{IMPACT_KIND_LABEL[i.impact_kind]}: {i.target_ref}</li>
        ))}
      </ul>
      {observation.supersedes_observation_key && (
        <p className="text-xs text-muted-foreground">{observation.supersedes_observation_key} を訂正した観測です。</p>
      )}
      <DecisionControls
        subjectKind="environment_observation"
        subjectKey={observation.observation_key}
        designStatus={observation.design_status}
        recheckState={observation.recheck_state}
        capturedDigest={observation.content_digest}
        digestReady
        testId={`observation-decision-${observation.observation_key}`}
      />
      <Button variant="ghost" size="sm" onClick={() => setCorrecting((v) => !v)} aria-expanded={correcting}>
        {correcting ? "訂正をやめる" : "訂正する"}
      </Button>
      {correcting && (
        <ObservationCreateForm
          stakeholderKey={stakeholderKey}
          needs={needs}
          supersedes={observation}
          onDone={() => setCorrecting(false)}
        />
      )}
    </li>
  );
}

export function ObservationSection({ stakeholderKey }: { stakeholderKey: string }) {
  const list = useEnvironmentObservations();
  const needsQuery = useStakeholderNeeds();
  const needs = useMemo(
    () => (needsQuery.data?.needs ?? []).filter((n) => n.stakeholder_key === stakeholderKey),
    [needsQuery.data, stakeholderKey],
  );
  const keys = (list.data?.observations ?? []).map((o) => o.observation_key);
  const details = useEnvironmentObservationDetails(keys);
  const needKeys = new Set(needs.map((n) => n.need_key));
  const loaded = details.map((d) => d.data).filter((d): d is EnvironmentObservationDetailOut => !!d);
  const mine = loaded.filter((o) => observationTouches(o, stakeholderKey, needKeys));
  const detailFailed = details.some((d) => d.isError);

  return (
    <section className="space-y-2" data-testid="stakeholder-observations">
      <h4 className="text-sm font-semibold">環境の観測</h4>
      {list.isLoading ? (
        <p className="text-xs text-muted-foreground">読み込んでいます…</p>
      ) : list.isError && !list.data ? (
        <QueryErrorState target="環境の観測一覧" error={list.error} onRetry={() => { void list.refetch(); }} retrying={list.isFetching} data-testid="stakeholder-observations-error" />
      ) : (
        <>
          {detailFailed && (
            <p className="text-xs text-amber-700" role="status">一部の観測の内容を取得できなかったため、この一覧は不完全な可能性があります。</p>
          )}
          {mine.length === 0 ? (
            <EmptyNote>この Stakeholder とその Need に影響する観測はまだありません。</EmptyNote>
          ) : (
            <ul className="space-y-2">
              {mine.map((o) => (
                <ObservationItem key={o.observation_key} observation={o} stakeholderKey={stakeholderKey} needs={needs} />
              ))}
            </ul>
          )}
        </>
      )}
      <details className="rounded border p-2">
        <summary className="cursor-pointer text-sm">環境の観測を追加</summary>
        <div className="mt-2"><ObservationCreateForm stakeholderKey={stakeholderKey} needs={needs} /></div>
      </details>
    </section>
  );
}

// --- Value Exchange ---------------------------------------------------------------

interface ExchangeFields {
  provider_stakeholder_key: string;
  receiver_stakeholder_key: string;
  exchange_kind: ValueNetworkExchangeKind;
  value_statement: string;
  consideration_state: ValueNetworkConsiderationState;
  consideration_kind: ValueNetworkExchangeKind | "";
  consideration_statement: string;
  channel: string;
  trigger: string;
  cadence: ValueNetworkCadence;
}

function exchangeDirty(a: ExchangeFields, b: ExchangeFields): boolean {
  return (Object.keys(a) as (keyof ExchangeFields)[]).some((k) => String(a[k]).trim() !== String(b[k]).trim());
}

function exchangeBody(f: ExchangeFields) {
  return {
    provider_stakeholder_key: f.provider_stakeholder_key,
    receiver_stakeholder_key: f.receiver_stakeholder_key,
    exchange_kind: f.exchange_kind,
    value_statement: f.value_statement.trim(),
    consideration_state: f.consideration_state,
    // A kind/statement only means something when a consideration exists;
    // `none` / `unknown` never carry one.
    consideration_kind: f.consideration_state === "present" && f.consideration_kind ? f.consideration_kind : null,
    consideration_statement: f.consideration_state === "present" ? f.consideration_statement.trim() : "",
    channel: f.channel.trim(),
    trigger: f.trigger.trim(),
    cadence: f.cadence,
  };
}

function stakeholderOptionLabel(n: ValueNetworkNodeOut): string {
  return `${n.display_name || n.stakeholder_key}(${DESIGN_STATUS_LABEL[n.design_status]})`;
}

function ExchangeFieldInputs({
  value,
  onChange,
  stakeholders,
}: {
  value: ExchangeFields;
  onChange: (next: ExchangeFields) => void;
  stakeholders: ValueNetworkNodeOut[];
}) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="提供する側" required>
          {(field) => (
            <Select {...field} value={value.provider_stakeholder_key} onChange={(e) => onChange({ ...value, provider_stakeholder_key: e.target.value })}>
              <option value="">(選択してください)</option>
              {stakeholders.map((n) => <option key={n.stakeholder_key} value={n.stakeholder_key}>{stakeholderOptionLabel(n)}</option>)}
            </Select>
          )}
        </FormField>
        <FormField label="受け取る側" required>
          {(field) => (
            <Select {...field} value={value.receiver_stakeholder_key} onChange={(e) => onChange({ ...value, receiver_stakeholder_key: e.target.value })}>
              <option value="">(選択してください)</option>
              {stakeholders.map((n) => <option key={n.stakeholder_key} value={n.stakeholder_key}>{stakeholderOptionLabel(n)}</option>)}
            </Select>
          )}
        </FormField>
      </div>
      <FormField label="渡すものの種類" hint="提供する側と受け取る側が同じ場合は「情報」だけを選べます。">
        {(field) => (
          <Select {...field} value={value.exchange_kind} onChange={(e) => onChange({ ...value, exchange_kind: e.target.value as ValueNetworkExchangeKind })}>
            {EXCHANGE_KIND_VALUES.map((k) => <option key={k} value={k}>{EXCHANGE_KIND_LABEL[k]}</option>)}
          </Select>
        )}
      </FormField>
      <FormField label="何を渡すか" required>
        {(field) => <Textarea {...field} rows={2} value={value.value_statement} onChange={(e) => onChange({ ...value, value_statement: e.target.value })} />}
      </FormField>
      <fieldset className="space-y-3 rounded border p-2">
        <legend className="px-1 text-xs font-medium">対価(受け取る側から返るもの)</legend>
        <p className="text-xs text-muted-foreground">
          対価は渡すものとは別に記録します。金額は記述として残すだけで、集計や計算は行いません。
        </p>
        <FormField label="対価の有無">
          {(field) => (
            <Select {...field} value={value.consideration_state} onChange={(e) => onChange({ ...value, consideration_state: e.target.value as ValueNetworkConsiderationState })}>
              {CONSIDERATION_STATE_VALUES.map((s) => <option key={s} value={s}>{CONSIDERATION_STATE_LABEL[s]}</option>)}
            </Select>
          )}
        </FormField>
        {value.consideration_state === "present" && (
          <>
            <FormField label="対価の種類" required>
              {(field) => (
                <Select {...field} value={value.consideration_kind} onChange={(e) => onChange({ ...value, consideration_kind: e.target.value as ValueNetworkExchangeKind | "" })}>
                  <option value="">(選択してください)</option>
                  {EXCHANGE_KIND_VALUES.map((k) => <option key={k} value={k}>{EXCHANGE_KIND_LABEL[k]}</option>)}
                </Select>
              )}
            </FormField>
            <FormField label="対価の記述" required>
              {(field) => <Textarea {...field} rows={2} value={value.consideration_statement} onChange={(e) => onChange({ ...value, consideration_statement: e.target.value })} />}
            </FormField>
          </>
        )}
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-3">
        <FormField label="経路(任意)">
          {(field) => <Input {...field} value={value.channel} onChange={(e) => onChange({ ...value, channel: e.target.value })} />}
        </FormField>
        <FormField label="きっかけ(任意)">
          {(field) => <Input {...field} value={value.trigger} onChange={(e) => onChange({ ...value, trigger: e.target.value })} />}
        </FormField>
        <FormField label="頻度">
          {(field) => (
            <Select {...field} value={value.cadence} onChange={(e) => onChange({ ...value, cadence: e.target.value as ValueNetworkCadence })}>
              {CADENCE_VALUES.map((c) => <option key={c} value={c}>{CADENCE_LABEL[c]}</option>)}
            </Select>
          )}
        </FormField>
      </div>
    </>
  );
}

const EMPTY_EXCHANGE: ExchangeFields = {
  provider_stakeholder_key: "", receiver_stakeholder_key: "", exchange_kind: "service", value_statement: "",
  consideration_state: "unknown", consideration_kind: "", consideration_statement: "",
  channel: "", trigger: "", cadence: "unknown",
};

export function ExchangeCreateForm({
  stakeholders,
  onCreated,
}: {
  stakeholders: ValueNetworkNodeOut[];
  onCreated: (exchangeKey: string) => void;
}) {
  const [key, setKey] = useState("");
  const [fields, setFields] = useState<ExchangeFields>(EMPTY_EXCHANGE);
  const create = useCreateValueExchange();
  const dirty = key.trim() !== "" || exchangeDirty(fields, EMPTY_EXCHANGE);
  useUnsavedChangesGuard(guardId("exchange-create"), dirty);

  if (stakeholders.length === 0) {
    return <EmptyNote testId="exchange-create-needs-stakeholder">Value Exchange を登録するには、先に Stakeholder を追加してください。</EmptyNote>;
  }
  return (
    <FormShell
      testId="exchange-create-form"
      onSubmit={() => create.mutate(
        { exchange_key: normalizeKey(key), ...exchangeBody(fields) },
        {
          onSuccess: (out) => {
            setKey("");
            setFields(EMPTY_EXCHANGE);
            onCreated(out.exchange_key);
          },
        },
      )}
      pending={create.isPending}
      submitLabel="Value Exchange を追加"
      canSubmit={key.trim() !== "" && fields.provider_stakeholder_key !== "" && fields.receiver_stakeholder_key !== ""}
      error={create.error}
    >
      <FormField label="key" required hint="この System の中で一意な識別子です(例: provide-service)。">
        {(field) => <Input {...field} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />}
      </FormField>
      <ExchangeFieldInputs value={fields} onChange={setFields} stakeholders={stakeholders} />
    </FormShell>
  );
}

function revisionToExchangeFields(r: ValueExchangeRevisionOut): ExchangeFields {
  return {
    provider_stakeholder_key: r.provider_stakeholder_key, receiver_stakeholder_key: r.receiver_stakeholder_key,
    exchange_kind: r.exchange_kind, value_statement: r.value_statement, consideration_state: r.consideration_state,
    consideration_kind: r.consideration_kind ?? "", consideration_statement: r.consideration_statement,
    channel: r.channel, trigger: r.trigger, cadence: r.cadence,
  };
}

function ExchangeRevisionForm({
  exchangeKey,
  seed,
  stakeholders,
}: {
  exchangeKey: string;
  seed: ExchangeFields;
  stakeholders: ValueNetworkNodeOut[];
}) {
  const [fields, setFields] = useState<ExchangeFields>(seed);
  const [changeNote, setChangeNote] = useState("");
  const save = useAddValueExchangeRevision();
  const dirty = exchangeDirty(fields, seed) || changeNote.trim() !== "";
  useUnsavedChangesGuard(guardId("exchange-revision", exchangeKey), dirty);
  return (
    <FormShell
      title="内容を編集"
      testId="exchange-revision-form"
      onSubmit={() => save.mutate({ exchangeKey, body: { ...exchangeBody(fields), change_note: changeNote.trim() } })}
      pending={save.isPending}
      submitLabel="新しい版として保存"
      canSubmit={dirty && fields.provider_stakeholder_key !== "" && fields.receiver_stakeholder_key !== ""}
      error={save.error}
    >
      <ExchangeFieldInputs value={fields} onChange={setFields} stakeholders={stakeholders} />
      <FormField label="変更の理由(任意)">
        {(field) => <Input {...field} value={changeNote} onChange={(e) => setChangeNote(e.target.value)} />}
      </FormField>
    </FormShell>
  );
}

export function ExchangeEditor({ exchangeKey, stakeholders }: { exchangeKey: string; stakeholders: ValueNetworkNodeOut[] }) {
  const detail = useValueExchangeDetail(exchangeKey);
  if (detail.isLoading) return <p className="text-xs text-muted-foreground">内容を読み込んでいます…</p>;
  if (detail.isError || !detail.data) return <QueryErrorState target="Value Exchange の内容" error={detail.error} onRetry={() => { void detail.refetch(); }} retrying={detail.isFetching} data-testid="exchange-detail-error" />;
  const d = detail.data;
  return (
    <div className="space-y-3" data-testid="exchange-editor">
      <DecisionControls
        subjectKind="value_exchange"
        subjectKey={exchangeKey}
        designStatus={d.design_status}
        recheckState={d.recheck_state}
        capturedDigest={d.current_revision?.content_digest ?? ""}
        digestReady
        testId="exchange-decision"
      />
      {d.current_revision && (
        <ExchangeRevisionForm
          key={`${exchangeKey}:${d.current_revision_id ?? "none"}`}
          exchangeKey={exchangeKey}
          seed={revisionToExchangeFields(d.current_revision)}
          stakeholders={stakeholders}
        />
      )}
    </div>
  );
}
