// Issue #409: Requirement list -> Requirement detail (statement, acceptance
// criteria, the Journey Steps it satisfies) -> the Solution Designs that
// target it (level 4's entry point; the adopted one's handoff view lives in
// the Solution Design tab, reached via `onOpenSolutionDesign`).

import { RequirementRevisionHistoryCard } from "./revision-history";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  useUxRequirements, useUxRequirementDetail, useCreateUxRequirement,
  useAddUxRequirementRevision, useAddUxRequirementStepLink,
  useUxJourneys, useUxJourneyDetail, useSolutionDesigns, useSolutionDesignDetailsBatch,
  useProductFeatures, useProductFeatureDetailsBatch, useCreateProductFeature,
  useAddProductFeatureRequirementLink, useDiscussionSaveRequest,
  useAddProductFeatureRevision, useRecordProductFeatureDecision,
} from "@/api/hooks";
import type {
  ProductFeatureDetailOut, ProductMilestoneDecisionKind,
  UxAcceptanceCriterionInput, UxRequirementKind, UxVerificationMethod,
} from "@/api/types";
import {
  DecisionConfirmDialog, RejectedNotice, rejectionFromError, useDecisionDialog, type Rejection,
} from "@/components/product-objective/decision-confirm";
import {
  DESIGN_STATUS_LABEL, DESIGN_RECHECK_STATE_LABEL, REQUIREMENT_KIND_LABEL,
  REF_RECHECK_STATE_LABEL, REF_TARGET_RESOLUTION_LABEL, VERIFICATION_METHOD_LABEL,
  featuresForRequirement, requirementAcceptsTargetLink, solutionDesignsForRequirement,
  sortRequirements, stepsInOrder,
} from "./model";
// The Product layer's OWN finite vocabularies (`ProductDesignStatus` /
// `ProductRecheckState`), reused verbatim rather than re-typed here: they are
// separate unions from `UxDesignStatus` / `UxDesignRecheckState` even where
// the values coincide, and a second copy of a label map is how the two drift.
import {
  DESIGN_STATUS_LABEL as PRODUCT_DESIGN_STATUS_LABEL,
  RECHECK_STATE_LABEL as PRODUCT_RECHECK_STATE_LABEL,
  REF_TARGET_RESOLUTION_LABEL as PRODUCT_REF_TARGET_RESOLUTION_LABEL,
} from "@/components/product-objective/model";
import {
  ArtifactReferencesCard, DegradedNote, DesignDecisionControls, EmptyNote,
  FieldErrorText, FormErrorBanner, LoadErrorCard, LoadingBlock, SectionHeading,
  StateBadge, useFieldRefs,
} from "./shared";
import { useFormValidation, useUiDraftSource, useUnsavedChangesGuard } from "@/lib/ui-draft";
import { clearFormDraftOpenRequest, peekPendingFormDraftPatch, useFormDraftOpenRequest, useFormDraftReceiver } from "@/lib/form-draft-inbox";
import { FormDraftConflictBanner } from "@/components/form-draft-conflict";
import { prepareAcceptanceCriteria } from "@/lib/acceptance-criteria-draft";
import { useIdempotentSaveRequest } from "@/lib/save-request";

const REQUIREMENT_KINDS: UxRequirementKind[] = ["functional", "non_functional", "constraint", "out_of_scope"];
const VERIFICATION_METHODS: UxVerificationMethod[] = [
  "manual_review", "replay", "experiment", "runtime_observation", "not_verifiable",
];

/** UX audit 2026-11-17 C11: a successful create SELECTS the new Requirement
 * (`onCreated`), so the developer lands on it instead of having to find it in
 * the list -- the same behaviour Objective / Milestone creation already has. */
function RequirementCreateForm({ onCreated }: { onCreated?: (requirementKey: string) => void }) {
  const create = useCreateUxRequirement();
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<UxRequirementKind>("functional");
  const validation = useFormValidation();
  const fieldRefs = useFieldRefs();
  const KNOWN_FIELDS = ["requirement_key"] as const;

  function submit() {
    const token = validation.begin();
    create.mutate(
      { requirement_key: key.trim(), requirement_kind: kind },
      {
        onSuccess: (out) => {
          validation.resolveSuccess(token);
          setKey("");
          toast.success("Requirement を作成しました");
          onCreated?.(out.requirement_key);
        },
        onError: (error) => {
          const apiError = error as ApiError;
          validation.resolveError(token, apiError, KNOWN_FIELDS);
          toast.error(apiError.detail || "作成できませんでした");
        },
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-requirement-create-form">
      <FormErrorBanner
        error={validation.formError}
        onFocusFirstField={
          Object.keys(validation.fieldErrors).length > 0
            ? () => fieldRefs.focus(Object.keys(validation.fieldErrors)[0])
            : undefined
        }
        testId="ux-requirement-create-form-error"
      />
      <Input
        ref={fieldRefs.register("requirement_key")}
        placeholder="requirement_key(例: checkout-single-page)"
        value={key}
        onChange={(e) => { setKey(e.target.value); validation.clearField("requirement_key"); }}
      />
      <FieldErrorText message={validation.fieldErrors.requirement_key?.message} />
      <Select value={kind} onChange={(e) => setKind(e.target.value as UxRequirementKind)}>
        {REQUIREMENT_KINDS.map((k) => (
          <option key={k} value={k}>
            {REQUIREMENT_KIND_LABEL[k]}
          </option>
        ))}
      </Select>
      <Button size="sm" disabled={!key.trim() || create.isPending} onClick={submit}>
        {create.isPending ? "作成中…" : "Requirement を作成する"}
      </Button>
    </div>
  );
}

function RequirementList({
  selectedKey,
  onSelect,
}: {
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const requirements = useUxRequirements();

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Requirement 一覧</CardTitle>
        <CardDescription>機能要件・非機能要件・制約・対象外。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <RequirementCreateForm onCreated={onSelect} />
        {requirements.isLoading ? (
          <LoadingBlock testId="ux-requirement-list-loading" />
        ) : requirements.isError ? (
          <LoadErrorCard onRetry={() => requirements.refetch()} testId="ux-requirement-list-error" />
        ) : (requirements.data?.requirements ?? []).length === 0 ? (
          <EmptyNote testId="ux-requirement-list-empty">この System にはまだ Requirement がありません。</EmptyNote>
        ) : (
          <>
            <DegradedNote
              sections={requirements.data?.degraded_sections ?? []}
              detail={requirements.data?.degraded_detail ?? {}}
            />
            <ul className="space-y-1" data-testid="ux-requirement-list" data-help-id="ux-design-studio.requirement.list">
              {sortRequirements(requirements.data?.requirements ?? []).map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    data-testid={`ux-requirement-item-${r.requirement_key}`}
                    onClick={() => onSelect(r.requirement_key)}
                    className={`w-full rounded border p-2 text-left text-sm ${
                      r.requirement_key === selectedKey ? "border-primary" : ""
                    }`}
                  >
                    <div className="font-mono">{r.requirement_key}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <StateBadge label={REQUIREMENT_KIND_LABEL[r.requirement_kind]} />
                      <StateBadge
                        label={DESIGN_STATUS_LABEL[r.design_status]}
                        tone={r.design_status === "confirmed" ? "success" : "outline"}
                      />
                      {r.recheck_state === "stale" && (
                        <StateBadge label={DESIGN_RECHECK_STATE_LABEL.stale} tone="warning" />
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function emptyCriterion(order: number): UxAcceptanceCriterionInput {
  return { criterion_key: "", criterion_order: order, statement: "", verification_method: "manual_review", verification_note: "" };
}

function RequirementRevisionForm({ requirementKey, onDone }: { requirementKey: string; onDone: () => void }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const detail = useUxRequirementDetail(requirementKey);
  const addRevision = useAddUxRequirementRevision(requirementKey);
  const current = detail.data?.current_revision ?? null;
  // Issue #445: frozen at mount, like `JourneyRevisionForm`'s seed -- a
  // refetch after this form's own submit must not retroactively change what
  // counts as "unedited" mid-session.
  const [seed] = useState(() => ({
    statement: current?.statement ?? "",
    rationale: current?.rationale ?? "",
    constraintText: current?.constraint_text ?? "",
    outOfScopeNote: current?.out_of_scope_note ?? "",
  }));
  const [statement, setStatement] = useState(current?.statement ?? "");
  const [rationale, setRationale] = useState(current?.rationale ?? "");
  const [constraintText, setConstraintText] = useState(current?.constraint_text ?? "");
  const [outOfScopeNote, setOutOfScopeNote] = useState(current?.out_of_scope_note ?? "");
  const [changeNote, setChangeNote] = useState("");

  // Issue #451 (§2.8): a rejected `POST .../revisions` now reaches both the
  // inline display below and (via `requirementFields[].validationError`) the
  // ui_draft context an AI conversation reads.
  const validation = useFormValidation();
  const fieldRefs = useFieldRefs();
  const REQUIREMENT_KNOWN_FIELDS = [
    "statement", "rationale", "constraint_text", "out_of_scope_note",
  ] as const;

  // Issue #445 (§2.2/§2.3): the `ux_requirement` draft. Acceptance criteria
  // are a #448 (ChildSpec) concern, not a top-level field here.
  const requirementFields = [
    { fieldName: "statement", value: statement, dirty: statement !== seed.statement, validationError: validation.fieldErrors.statement?.message ?? "" },
    { fieldName: "rationale", value: rationale, dirty: rationale !== seed.rationale, validationError: validation.fieldErrors.rationale?.message ?? "" },
    { fieldName: "constraint_text", value: constraintText, dirty: constraintText !== seed.constraintText, validationError: validation.fieldErrors.constraint_text?.message ?? "" },
    { fieldName: "out_of_scope_note", value: outOfScopeNote, dirty: outOfScopeNote !== seed.outOfScopeNote, validationError: validation.fieldErrors.out_of_scope_note?.message ?? "" },
  ];


  const [seedCriteria] = useState(() => (current?.acceptance_criteria ?? []).map((c) => ({
    criterion_key: c.criterion_key, criterion_order: c.criterion_order, statement: c.statement,
    verification_method: c.verification_method, verification_note: c.verification_note,
  })));
  const [criteria, setCriteria] = useState<UxAcceptanceCriterionInput[]>(
    seedCriteria.length > 0 ? seedCriteria : [],
  );


  // Issue #446 (§3.2/§3.3): receive a prefilled change candidate. Every
  // clean field lands immediately through its own setter; a field that is
  // dirty AND differs from the proposal is held back until the developer
  // picks 「このまま」/「提案で置き換える」 for it.
  const draftReceiver = useFormDraftReceiver("ux_requirement.revision", requirementKey, {
    statement: { value: statement, dirty: statement !== seed.statement, setValue: setStatement },
    rationale: { value: rationale, dirty: rationale !== seed.rationale, setValue: setRationale },
    constraint_text: { value: constraintText, dirty: constraintText !== seed.constraintText, setValue: setConstraintText },
    out_of_scope_note: { value: outOfScopeNote, dirty: outOfScopeNote !== seed.outOfScopeNote, setValue: setOutOfScopeNote },
  }, undefined, (patch) => prepareAcceptanceCriteria(patch, criteria, seedCriteria, setCriteria));


  function updateCriterion(i: number, patch: Partial<UxAcceptanceCriterionInput>) {
    validation.clearField("acceptance_criteria");
    setCriteria((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }

  // Issue #452 §3.6/§3.7: an idempotent `save_request_id` bound to this
  // submit's exact content. A retry with UNCHANGED content (the same
  // 「版を保存する」 click again after a failure/lost response) reuses the
  // SAME id, so the server can recognise it as the same attempt rather than
  // creating a second revision; editing any field first mints a fresh id
  // (the Decisions: "ユーザーが編集を変えたら新しい保存要求として扱う").
  const saveRequest = useIdempotentSaveRequest();
  // Manual (`enabled: false`) -- this query only runs when the developer
  // explicitly asks "状態を確認する" after a request whose response was
  // lost, never automatically (§3.7: "類似本文検索で成功と推測しない" --
  // this is the opposite of guessing: an explicit, server-confirmed check).
  const saveRequestQuery = useDiscussionSaveRequest(saveRequest.activeId, { enabled: false });
  const [responseLost, setResponseLost] = useState(false);

  useUiDraftSource("ux_requirement.revision", requirementKey, () => ({
    hasUnsavedChanges: requirementFields.some((f) => f.dirty) || JSON.stringify(criteria) !== JSON.stringify(seedCriteria) || !!changeNote,
    saveResultUnknown: responseLost,
    saveReferenceId: saveRequest.activeId,
    validationState: validation.status,
    sectionErrors: validation.formError ? [validation.formError] : [],
    fields: requirementFields,
    selectedItemRef: "",
    activeTab: "",
    comparisonTarget: "",
    localRevisionToken: JSON.stringify(requirementFields.map((f) => [f.fieldName, f.value])),
  }));

  function currentSaveDigest(): string {
    return JSON.stringify([statement, rationale, constraintText, outOfScopeNote, changeNote, criteria]);
  }

  const liveSaveDigest = useRef("");
  const submittedDigest = useRef("");
  useEffect(() => { liveSaveDigest.current = currentSaveDigest(); });

  function submit() {
    const token = validation.begin();
    const digest = currentSaveDigest();
    submittedDigest.current = digest;
    const saveRequestId = saveRequest.idFor(digest);
    const params = new URLSearchParams(searchParams);
    params.set("save_request", saveRequestId);
    setSearchParams(params, { replace: true });
    setResponseLost(false);
    addRevision.mutate(
      {
        statement, rationale, constraint_text: constraintText, out_of_scope_note: outOfScopeNote,
        change_note: changeNote, acceptance_criteria: criteria, save_request_id: saveRequestId,
      },
      {
        onSuccess: () => {
          validation.resolveSuccess(token);
          toast.success("Requirement の版を追加しました");
          saveRequest.clear();
          if (liveSaveDigest.current === digest) onDone();
          else toast.info("保存中に編集した入力を保持しています。追加の変更はまだ保存されていません。");
        },
        onError: (error) => {
          const apiError = error as ApiError;
          // A structured 4xx (validation, conflict, ...) is a definite
          // outcome -- the draft stays, and a plain retry (same content,
          // same id) is the right recovery. A network-level failure (no
          // structured `code`) means the OUTCOME is unknown -- the domain
          // write may have actually committed -- so offer "状態を確認する"
          // instead of only "retry" (§3.7 / DD-UX-07's "結果不明なら先に
          // 保存状態を再取得").
          setResponseLost(!apiError.code);
          validation.resolveError(token, apiError, REQUIREMENT_KNOWN_FIELDS);
          toast.error(apiError.detail || "追加できませんでした");
        },
      },
    );
  }

  async function confirmSaveState() {
    const result = await saveRequestQuery.refetch();
    if (result.isError) {
      const error = result.error as ApiError;
      if (error.status === 404 && error.code === "discussion_save_request_not_found") {
        toast.info("保存要求は未登録です。同じ要求でもう一度保存できます。");
      } else {
        toast.error("保存結果を確認できませんでした。入力を保持しています。もう一度状態を確認してください。");
      }
      return;
    }
    if (result.data?.status === "succeeded") {
      toast.success("前回の保存はサーバーで完了していました。");
      saveRequest.clear();
      setResponseLost(false);
      if (liveSaveDigest.current === submittedDigest.current) onDone();
      return;
    }
    if (result.data?.status === "failed") {
      toast.error("前回の保存要求は失敗していました。内容はそのまま保持しています。");
      return;
    }
    // 404 (`discussion_save_request_not_found`): the id never reached the
    // server -- safe to retry with the SAME id (never a new one, per §3.6).
    toast.info("保存結果はまだ確定していません。もう一度状態を確認してください。");
  }

  // §2.8.2: `section === "acceptance_criteria"` (e.g. a duplicated
  // criterion_key, or out_of_scope_requirement_not_verifiable which carries
  // no specific field at all) belongs near 受入条件, not at the top.
  const topLevelFormError =
    validation.formError && validation.formError.section !== "acceptance_criteria" ? validation.formError : null;
  const criteriaFormError =
    validation.formError?.section === "acceptance_criteria" ? validation.formError : null;
  const firstInvalidRequirementField = REQUIREMENT_KNOWN_FIELDS.find((f) => validation.fieldErrors[f]);

  return (
    <div className="space-y-3 rounded border p-3" data-testid="ux-requirement-revision-form">
      <FormDraftConflictBanner conflicts={draftReceiver.conflicts} onResolve={draftReceiver.resolveField} />
      <FormErrorBanner
        error={topLevelFormError}
        onFocusFirstField={
          firstInvalidRequirementField ? () => fieldRefs.focus(firstInvalidRequirementField) : undefined
        }
        testId="ux-requirement-revision-form-error"
      />
      <div>
        <Textarea
          ref={fieldRefs.register("statement")}
          placeholder="要件の文"
          value={statement}
          onChange={(e) => { setStatement(e.target.value); validation.clearField("statement"); }}
          rows={2}
        />
        <FieldErrorText message={validation.fieldErrors.statement?.message} />
      </div>
      <div>
        <Textarea
          ref={fieldRefs.register("rationale")}
          placeholder="理由(rationale)"
          value={rationale}
          onChange={(e) => { setRationale(e.target.value); validation.clearField("rationale"); }}
          rows={2}
        />
        <FieldErrorText message={validation.fieldErrors.rationale?.message} />
      </div>
      <div>
        <Input
          ref={fieldRefs.register("constraint_text")}
          placeholder="制約(constraint_text)"
          value={constraintText}
          onChange={(e) => { setConstraintText(e.target.value); validation.clearField("constraint_text"); }}
        />
        <FieldErrorText message={validation.fieldErrors.constraint_text?.message} />
      </div>
      <div>
        <Input
          ref={fieldRefs.register("out_of_scope_note")}
          placeholder="対象外にした理由(out_of_scope_note)"
          value={outOfScopeNote}
          onChange={(e) => { setOutOfScopeNote(e.target.value); validation.clearField("out_of_scope_note"); }}
        />
        <FieldErrorText message={validation.fieldErrors.out_of_scope_note?.message} />
      </div>
      <Input placeholder="変更メモ(任意)" value={changeNote} onChange={(e) => setChangeNote(e.target.value)} />

      <div className="space-y-2">
        <SectionHeading as="h4">受入条件</SectionHeading>
        <FormErrorBanner error={criteriaFormError} testId="ux-requirement-criteria-form-error" />
        {criteria.map((c, i) => (
          <div key={i} className="space-y-1 rounded border p-2 text-xs">
            <div className="flex gap-2">
              <Input
                placeholder="criterion_key"
                value={c.criterion_key}
                onChange={(e) => updateCriterion(i, { criterion_key: e.target.value })}
              />
              <Select
                value={c.verification_method}
                onChange={(e) => updateCriterion(i, { verification_method: e.target.value as UxVerificationMethod })}
              >
                {VERIFICATION_METHODS.map((m) => (
                  <option key={m} value={m}>
                    {VERIFICATION_METHOD_LABEL[m]}
                  </option>
                ))}
              </Select>
            </div>
            <Input
              placeholder="文言(statement)"
              value={c.statement}
              onChange={(e) => updateCriterion(i, { statement: e.target.value })}
            />
            <Input aria-label={`受入条件 ${c.criterion_key} の順序`} type="number" value={c.criterion_order}
              onChange={(e) => updateCriterion(i, { criterion_order: Number(e.target.value) })} />
            <Input aria-label={`受入条件 ${c.criterion_key} の検証メモ`} value={c.verification_note}
              onChange={(e) => updateCriterion(i, { verification_note: e.target.value })} />
            <Button variant="ghost" size="sm" onClick={() => setCriteria((prev) => prev.filter((_, idx) => idx !== i))}>
              削除
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setCriteria((prev) => [...prev, emptyCriterion(prev.length + 1)])}
        >
          受入条件を追加
        </Button>
      </div>

      {responseLost && (
        <div
          className="space-y-1 rounded border border-amber-300 bg-amber-50 p-2 text-xs dark:border-amber-800 dark:bg-amber-950"
          role="status"
          data-testid="ux-requirement-save-response-lost"
        >
          <p>
            通信状況により、保存が完了したかどうか分かりません。サーバー側の状態を確認してから、必要であれば再試行してください。
          </p>
          <div className="flex gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              onClick={confirmSaveState}
              disabled={saveRequestQuery.isFetching}
              data-testid="ux-requirement-save-request-confirm"
            >
              {saveRequestQuery.isFetching ? "確認中…" : "状態を確認する"}
            </Button>
          </div>
        </div>
      )}
      <div className="flex gap-2">
        <Button size="sm" disabled={addRevision.isPending || draftReceiver.conflicts.length > 0} onClick={submit} data-testid="ux-requirement-revision-submit">
          {addRevision.isPending ? "保存中…" : responseLost ? "再試行する" : "版を保存する"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          キャンセル
        </Button>
      </div>
    </div>
  );
}

/** Requirement -> Journey Step link. `initialJourneyKey` / `initialStepKey`
 * preselect the pair when the form is opened FROM a Step (UX audit C11), so
 * the developer does not pick again what they were already looking at; both
 * stay editable. */
export function StepLinkForm({
  requirementKey, onDone, initialJourneyKey = "", initialStepKey = "",
}: {
  requirementKey: string;
  onDone: () => void;
  initialJourneyKey?: string;
  initialStepKey?: string;
}) {
  const journeys = useUxJourneys();
  const [journeyKey, setJourneyKey] = useState(initialJourneyKey);
  const journeyDetail = useUxJourneyDetail(journeyKey || null);
  const [stepKey, setStepKey] = useState(initialStepKey);
  const [note, setNote] = useState("");
  const addLink = useAddUxRequirementStepLink(requirementKey);
  const steps = stepsInOrder(journeyDetail.data?.current_revision);

  function submit() {
    addLink.mutate(
      { journey_key: journeyKey, step_key: stepKey, note },
      {
        onSuccess: () => {
          toast.success("Step への紐づけを追加しました");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="ux-requirement-step-link-form">
      <Select aria-label="紐づける Journey" value={journeyKey} onChange={(e) => { setJourneyKey(e.target.value); setStepKey(""); }}>
        <option value="">Journey を選択</option>
        {(journeys.data?.journeys ?? []).map((j) => (
          <option key={j.journey_key} value={j.journey_key}>
            {j.journey_key}
          </option>
        ))}
      </Select>
      {journeyKey && (
        <Select aria-label="紐づける Step" value={stepKey} onChange={(e) => setStepKey(e.target.value)}>
          <option value="">Step を選択</option>
          {steps.map((s) => (
            <option key={s.step_key} value={s.step_key}>
              {s.step_key}: {s.user_intent}
            </option>
          ))}
        </Select>
      )}
      <Input placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" disabled={!journeyKey || !stepKey || addLink.isPending} onClick={submit} data-testid="ux-requirement-step-link-submit">
        {addLink.isPending ? "追加中…" : "紐づける"}
      </Button>
    </div>
  );
}

/**
 * UX audit 2026-11-17 C11: create a Requirement FROM a Journey Step and link
 * it to that Step, without leaving the Step. Two explicit submissions, in
 * order: the create (the same endpoint the Requirement tab uses), then the
 * existing `StepLinkForm` with this Journey and Step preselected. After the
 * link is recorded the new Requirement is opened (`onLinked`).
 */
export function RequirementCreateAndLinkForm({
  journeyKey, stepKey, onLinked, onCancel,
}: {
  journeyKey: string;
  stepKey: string;
  onLinked: (requirementKey: string) => void;
  onCancel: () => void;
}) {
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  return (
    <div className="space-y-2 rounded border p-2" data-testid="ux-step-requirement-create-and-link">
      {createdKey === null ? (
        <>
          <p className="text-xs font-semibold">1. Requirement を作成する</p>
          <RequirementCreateForm onCreated={setCreatedKey} />
        </>
      ) : (
        <>
          <p className="text-xs font-semibold" data-testid="ux-step-requirement-created">
            2. 作成した Requirement「{createdKey}」をこの Step に紐づける
          </p>
          <StepLinkForm
            requirementKey={createdKey}
            initialJourneyKey={journeyKey}
            initialStepKey={stepKey}
            onDone={() => onLinked(createdKey)}
          />
        </>
      )}
      <Button size="sm" variant="ghost" onClick={onCancel}>閉じる</Button>
    </div>
  );
}

function SolutionDesignsForRequirement({
  requirementId,
  onOpenSolutionDesign,
}: {
  requirementId: number;
  onOpenSolutionDesign: (key: string) => void;
}) {
  const designs = useSolutionDesigns();
  const allKeys = (designs.data?.designs ?? []).map((d) => d.design_key);
  const details = useSolutionDesignDetailsBatch(allKeys);
  const loaded = details.filter((d) => d.data).map((d) => d.data!);
  const anyLoading = designs.isLoading || details.some((d) => d.isLoading);
  const anyError = designs.isError || details.some((d) => d.isError);
  const linked = solutionDesignsForRequirement(loaded, requirementId);

  return (
    <div className="space-y-2" data-testid="ux-requirement-solution-designs">
      <SectionHeading>この Requirement を満たす Solution Design</SectionHeading>
      {anyLoading ? (
        <LoadingBlock testId="ux-requirement-solution-designs-loading" />
      ) : anyError ? (
        <EmptyNote testId="ux-requirement-solution-designs-unavailable">
          Solution Design の一部を取得できなかったため、紐づきを完全には表示できません。
        </EmptyNote>
      ) : linked.length === 0 ? (
        <EmptyNote testId="ux-requirement-solution-designs-empty">
          まだこの Requirement を満たす Solution Design がありません。
        </EmptyNote>
      ) : (
        <ul className="space-y-1 text-xs">
          {linked.map(({ design }) => (
            <li key={design.id}>
              <button
                type="button"
                className="w-full rounded border p-2 text-left hover:bg-accent"
                onClick={() => onOpenSolutionDesign(design.design_key)}
                data-testid={`ux-requirement-solution-design-link-${design.design_key}`}
              >
                <div className="font-mono">{design.design_key}</div>
                <div className="mt-1">
                  {design.adopted_option_key ? (
                    <StateBadge label={`採用済み Option: ${design.adopted_option_key}`} tone="success" />
                  ) : (
                    <StateBadge label="まだ Option は採用されていません" />
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const FEATURE_DECISION_VALUES: ProductMilestoneDecisionKind[] = ["confirm", "reject", "retire", "reinstate"];
const FEATURE_DECISION_LABEL: Record<ProductMilestoneDecisionKind, string> = {
  confirm: "確定する",
  reject: "却下する",
  retire: "廃止する",
  reinstate: "再提案に戻す",
};

/**
 * UX audit 2026-11-17 C2: a Feature's content (append-only revision) and its
 * definition decision, on the Requirement where the developer meets it.
 * Before this a Feature could only be created by key and then stayed
 * 「未確定」 forever, because no screen could save a version or confirm it.
 *
 * The revision form is seeded from the CURRENT revision (the endpoint stores
 * a full snapshot, #427 review) and the component is keyed per Feature, so
 * text typed for one Feature never lands on another. The decision goes
 * through the confirmation dialog and sends the current revision's
 * `content_digest`; no transition table is held here -- a refusal is the
 * server's `product_feature_not_decidable`, shown with the current state.
 */
function FeatureWorkPanel({ feature }: { feature: ProductFeatureDetailOut }) {
  const current = feature.current_revision;
  const addRevision = useAddProductFeatureRevision(feature.feature_key);
  const record = useRecordProductFeatureDecision(feature.feature_key);
  const dialog = useDecisionDialog<ProductMilestoneDecisionKind>();
  const [title, setTitle] = useState(current?.title ?? "");
  const [statement, setStatement] = useState(current?.statement ?? "");
  const [rationale, setRationale] = useState(current?.rationale ?? "");
  const [scopeNote, setScopeNote] = useState(current?.scope_note ?? "");
  const [summary, setSummary] = useState(current?.summary ?? "");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const dirty =
    title !== (current?.title ?? "")
    || statement !== (current?.statement ?? "")
    || rationale !== (current?.rationale ?? "")
    || scopeNote !== (current?.scope_note ?? "")
    || summary !== (current?.summary ?? "");
  useUnsavedChangesGuard(`product-feature-revision:${feature.feature_key}`, dirty);
  const stateLabel = PRODUCT_DESIGN_STATUS_LABEL[feature.design_status];

  function saveRevision() {
    setRejection(null);
    addRevision.mutate(
      { title, statement, rationale, scope_note: scopeNote, summary },
      {
        onSuccess: () => toast.success("Feature の版を保存しました"),
        onError: (error) => setRejection(rejectionFromError(error, "版を保存できませんでした")),
      },
    );
  }

  function decide(decision: ProductMilestoneDecisionKind, reason: string) {
    setRejection(null);
    record.mutate(
      { decision, rationale: reason, captured_digest: current?.content_digest ?? "" },
      {
        onSuccess: () => { dialog.close(); toast.success(`Feature を「${FEATURE_DECISION_LABEL[decision]}」として記録しました`); },
        onError: (error) => {
          dialog.close();
          setRejection(rejectionFromError(error, "記録できませんでした", `現在の状態: ${stateLabel}`));
        },
      },
    );
  }

  return (
    <div className="mt-2 space-y-2" data-testid={`ux-requirement-feature-work-${feature.feature_key}`}>
      <p className="text-xs font-semibold">版を保存する(新しい版として追記されます)</p>
      <Input aria-label="Feature のタイトル" placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea aria-label="Feature の内容" placeholder="この Feature が提供すること(statement)" value={statement} onChange={(e) => setStatement(e.target.value)} rows={2} />
      <Textarea aria-label="Feature の理由" placeholder="理由(rationale)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <Input aria-label="Feature の範囲" placeholder="範囲の補足(scope_note)" value={scopeNote} onChange={(e) => setScopeNote(e.target.value)} />
      <Textarea aria-label="Feature の要約" placeholder="要約" value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} />
      <Button size="sm" variant="outline" disabled={addRevision.isPending || !dirty} onClick={saveRevision} data-testid={`ux-requirement-feature-revision-submit-${feature.feature_key}`}>
        {addRevision.isPending ? "保存中…" : "版を保存する"}
      </Button>

      <div className="space-y-1 border-t pt-2">
        <p className="text-xs font-semibold">Feature の定義を確定する(人間の判断として記録されます)</p>
        <p className="text-xs" data-testid={`ux-requirement-feature-state-${feature.feature_key}`}>現在の状態: {stateLabel}</p>
        {dirty && (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            保存していない変更があります。確定は保存済みの版に対して記録されます。
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {FEATURE_DECISION_VALUES.map((d) => (
            <Button
              key={d} size="sm" variant="outline" disabled={record.isPending}
              onClick={() => dialog.open(d)}
              data-testid={`ux-requirement-feature-decision-${feature.feature_key}-${d}`}
            >
              {FEATURE_DECISION_LABEL[d]}
            </Button>
          ))}
        </div>
      </div>
      {rejection && <RejectedNotice {...rejection} testId={`ux-requirement-feature-rejected-${feature.feature_key}`} />}
      {dialog.pendingKind && (
        <DecisionConfirmDialog
          open
          onOpenChange={(open) => { if (!open) dialog.close(); }}
          title={`Feature「${feature.title || feature.feature_key}」を${FEATURE_DECISION_LABEL[dialog.pendingKind]}`}
          currentStateText={`現在の状態: ${stateLabel}${current ? `(第 ${current.revision_number} 版)` : "(まだ版がありません)"}`}
          consequence="判断の記録だけを追加します。実装・publish・Solution Design の採用は変わりません。前の判断は履歴に残ります。"
          confirmLabel={FEATURE_DECISION_LABEL[dialog.pendingKind]}
          pending={record.isPending}
          onConfirm={(reason) => decide(dialog.pendingKind!, reason)}
        />
      )}
    </div>
  );
}

/**
 * Epic #427 §7.2's Requirement -> Feature link, and the ONLY editing surface
 * for it (`POST /product-features/{key}/requirement-links`). It lives on the
 * Requirement because that is the subject the developer has open; the link
 * itself is stored on the FEATURE, which is why the linked set is computed by
 * reading every Feature's own links (`featuresForRequirement`).
 *
 * This is the operation the Overview's `link_requirement_to_feature` next
 * step names, so it has to be COMPLETABLE here -- an explicit submit that
 * records the link, not merely a screen that mentions Features. Before this
 * existed the Feature layer had a complete server and no surface at all, so
 * that CTA could only ever open a screen.
 *
 * Creating a Feature and linking it are two separate explicit submissions,
 * both `decision_method: manual` server-side. Neither adopts, applies, or
 * publishes anything (§0: the design layer never touches implementation).
 */
function FeaturesForRequirement({
  requirementKey,
  requirementId,
}: {
  requirementKey: string;
  requirementId: number;
}) {
  const features = useProductFeatures();
  const allKeys = (features.data?.features ?? []).map((f) => f.feature_key);
  const details = useProductFeatureDetailsBatch(allKeys);
  const loaded = details.filter((d) => d.data).map((d) => d.data!);
  const anyLoading = features.isLoading || details.some((d) => d.isLoading);
  const anyError = features.isError || details.some((d) => d.isError);
  const linked = featuresForRequirement(loaded, requirementId);
  const linkedKeys = new Set(linked.map((l) => l.feature.feature_key));
  const linkable = allKeys.filter((k) => !linkedKeys.has(k));

  const [featureKey, setFeatureKey] = useState("");
  const [newFeatureKey, setNewFeatureKey] = useState("");
  const addLink = useAddProductFeatureRequirementLink(featureKey || null);
  const createFeature = useCreateProductFeature();

  function submitLink() {
    addLink.mutate(
      { requirement_key: requirementKey },
      {
        onSuccess: () => {
          setFeatureKey("");
          toast.success("Feature との対応を記録しました");
        },
        onError: (error) => toast.error((error as ApiError).detail || "記録できませんでした"),
      },
    );
  }

  function submitCreate() {
    const key = newFeatureKey.trim();
    createFeature.mutate(
      { feature_key: key },
      {
        onSuccess: () => {
          setNewFeatureKey("");
          // Preselect the Feature just created: the developer's next step is
          // linking it, and making them find it again in the list is the
          // dead end this whole section exists to remove.
          setFeatureKey(key);
          toast.success("Feature を作成しました");
        },
        onError: (error) => toast.error((error as ApiError).detail || "作成できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-requirement-features">
      <SectionHeading as="h3">この Requirement を実現する Feature</SectionHeading>
      {anyLoading ? (
        <LoadingBlock testId="ux-requirement-features-loading" />
      ) : anyError ? (
        // 「取得できなかった」と「1 件もない」は別の答え (§0 invariant 8)。
        <EmptyNote testId="ux-requirement-features-unavailable">
          Feature の一部を取得できなかったため、対応を完全には表示できません。
        </EmptyNote>
      ) : linked.length === 0 ? (
        <EmptyNote testId="ux-requirement-features-empty">
          まだこの Requirement に対応する Feature がありません。
        </EmptyNote>
      ) : (
        <ul className="space-y-1 text-xs" data-testid="ux-requirement-feature-list">
          {linked.map(({ feature, link }) => (
            <li key={link.id} className="rounded border p-2" data-testid={`ux-requirement-feature-${feature.feature_key}`}>
              <div className="font-mono">{feature.feature_key}</div>
              {feature.title && <div className="mt-0.5">{feature.title}</div>}
              <div className="mt-1 flex flex-wrap gap-1">
                <StateBadge
                  label={PRODUCT_DESIGN_STATUS_LABEL[feature.design_status]}
                  tone={feature.design_status === "confirmed" ? "success" : "outline"}
                />
                {/* `target_resolution` and `recheck_state` are two axes
                    (§7.2): the first says whether the link still reaches this
                    Requirement at all, the second whether its content moved
                    since. `recheck_state` has no `unresolved` member, so it
                    cannot stand in -- an unresolved link would read merely
                    「再確認が必要」. */}
                <StateBadge
                  label={PRODUCT_REF_TARGET_RESOLUTION_LABEL[link.target_resolution]}
                  tone={link.target_resolution === "resolved" ? "success" : "destructive"}
                />
                <StateBadge
                  label={PRODUCT_RECHECK_STATE_LABEL[link.recheck_state]}
                  tone={link.recheck_state === "current" ? "outline" : "warning"}
                />
              </div>
              {/* Open by default while the Feature has no version yet or is
                  still unconfirmed -- that is exactly when it needs work. */}
              <details
                className="mt-1"
                open={feature.current_revision === null || feature.design_status === "proposed"}
                data-testid={`ux-requirement-feature-details-${feature.feature_key}`}
              >
                <summary className="cursor-pointer text-xs text-primary">版の保存と確定</summary>
                <FeatureWorkPanel key={feature.feature_key} feature={feature} />
              </details>
            </li>
          ))}
        </ul>
      )}

      {!anyLoading && !anyError && (
        <div className="space-y-2 rounded border p-2" data-testid="ux-requirement-feature-link-form">
          <p className="text-xs font-semibold">Feature に対応づける</p>
          {linkable.length === 0 ? (
            <p className="text-xs text-muted-foreground" data-testid="ux-requirement-feature-none-linkable">
              {allKeys.length === 0
                ? "この System にはまだ Feature がありません。下で作成してください。"
                : "この System の Feature はすべてこの Requirement に対応づけ済みです。"}
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Select
                aria-label="対応づける Feature"
                value={featureKey}
                onChange={(e) => setFeatureKey(e.target.value)}
              >
                <option value="">Feature を選択</option>
                {linkable.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                disabled={!featureKey || addLink.isPending}
                onClick={submitLink}
                data-testid="ux-requirement-feature-link-submit"
              >
                {addLink.isPending ? "記録中…" : "対応づける"}
              </Button>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t pt-2">
            <Input
              placeholder="feature_key(例: single-page-checkout)"
              value={newFeatureKey}
              onChange={(e) => setNewFeatureKey(e.target.value)}
              className="max-w-xs"
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!newFeatureKey.trim() || createFeature.isPending}
              onClick={submitCreate}
              data-testid="ux-requirement-feature-create-submit"
            >
              {createFeature.isPending ? "作成中…" : "Feature を作成する"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function RequirementDetail({
  requirementKey,
  onOpenSolutionDesign,
}: {
  requirementKey: string;
  onOpenSolutionDesign: (key: string) => void;
}) {
  const detail = useUxRequirementDetail(requirementKey);
  // Issue #446: auto-open the revision form when the review UI just
  // delivered a prefill patch for THIS Requirement (the normal "navigate,
  // then the destination form is ready" case, §3.6) -- otherwise the patch
  // would sit unconsumed until the developer manually opens 「版を追加する」.
  const [revisionOpen, setRevisionOpen] = useState(() =>
    peekPendingFormDraftPatch("ux_requirement.revision", requirementKey),
  );
  const revisionRequested = useFormDraftOpenRequest("ux_requirement.revision", requirementKey);
  const [linkOpen, setLinkOpen] = useState(false);

  if (detail.isLoading) return <LoadingBlock testId="ux-requirement-detail-loading" />;
  if (detail.isError || !detail.data) {
    return <LoadErrorCard onRetry={() => detail.refetch()} testId="ux-requirement-detail-error" />;
  }
  const r = detail.data;

  return (
    <div className="space-y-4" data-testid="ux-requirement-detail" data-help-id="ux-design-studio.requirement.detail">
      <DegradedNote sections={r.degraded_sections} detail={r.degraded_detail} />
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-mono text-base font-semibold">{r.requirement_key}</h2>
        <StateBadge label={REQUIREMENT_KIND_LABEL[r.requirement_kind]} />
        <StateBadge
          label={DESIGN_STATUS_LABEL[r.design_status]}
          tone={r.design_status === "confirmed" ? "success" : "outline"}
        />
        <StateBadge
          label={DESIGN_RECHECK_STATE_LABEL[r.recheck_state]}
          tone={r.recheck_state === "stale" ? "warning" : "outline"}
        />
      </div>
      {!requirementAcceptsTargetLink(r) && (
        <p className="text-xs text-muted-foreground" data-testid="ux-requirement-out-of-scope-note">
          対象外(out_of_scope)の要件です。実装対象へは接続できません。
        </p>
      )}

      {r.current_revision ? (
        <div className="rounded border p-3 text-sm" data-testid="ux-requirement-current-revision">
          <p>{r.current_revision.statement || "(要件の文が未記入です)"}</p>
          {r.current_revision.acceptance_criteria.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {r.current_revision.acceptance_criteria.map((c) => (
                <li key={c.criterion_key}>
                  {c.statement} ({VERIFICATION_METHOD_LABEL[c.verification_method]})
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <EmptyNote testId="ux-requirement-no-revision">まだ版がありません。版を追加してください。</EmptyNote>
      )}
      <Button variant="outline" size="sm" onClick={() => {
        setRevisionOpen(!(revisionOpen || revisionRequested));
        clearFormDraftOpenRequest("ux_requirement.revision", requirementKey);
      }}>
        {revisionOpen || revisionRequested ? "版の追加を閉じる" : r.current_revision ? "版を追加する" : "最初の版を作成する"}
      </Button>
      {(revisionOpen || revisionRequested) && (
        <RequirementRevisionForm requirementKey={requirementKey} onDone={() => {
          setRevisionOpen(false);
          clearFormDraftOpenRequest("ux_requirement.revision", requirementKey);
        }} />
      )}

      <div>
        <SectionHeading as="h3">紐づく Journey Step</SectionHeading>
        {r.step_links.filter((l) => l.superseded_by_id === null).length === 0 ? (
          <EmptyNote>まだ Step への紐づけがありません。</EmptyNote>
        ) : (
          <ul className="mt-2 space-y-1 text-xs">
            {r.step_links
              .filter((l) => l.superseded_by_id === null)
              .map((l) => (
                <li key={l.id} className="rounded border p-2">
                  <div className="font-mono">{l.journey_key ?? l.journey_id} / {l.step_key}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <StateBadge
                      label={REF_TARGET_RESOLUTION_LABEL[l.target_resolution]}
                      tone={l.target_resolution === "resolved" ? "success" : "destructive"}
                    />
                    <StateBadge
                      label={REF_RECHECK_STATE_LABEL[l.recheck_state]}
                      tone={l.recheck_state === "current" ? "outline" : "warning"}
                    />
                  </div>
                </li>
              ))}
          </ul>
        )}
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => setLinkOpen((v) => !v)}>
          {linkOpen ? "紐づけの追加を閉じる" : "Step に紐づける"}
        </Button>
        {linkOpen && <StepLinkForm requirementKey={requirementKey} onDone={() => setLinkOpen(false)} />}
      </div>

      <RequirementRevisionHistoryCard requirementKey={requirementKey} />

      <FeaturesForRequirement requirementKey={requirementKey} requirementId={r.id} />

      <SolutionDesignsForRequirement requirementId={r.id} onOpenSolutionDesign={onOpenSolutionDesign} />

      <ArtifactReferencesCard subjectKind="requirement" subjectKey={requirementKey} artifacts={r.artifact_references} />

      <DesignDecisionControls
        subjectKind="requirement"
        subjectKey={requirementKey}
        capturedDigest={r.current_revision?.content_digest ?? ""}
        decisions={r.decisions}
      />
    </div>
  );
}

export function RequirementPanel({
  selectedKey,
  onSelect,
  onOpenSolutionDesign,
}: {
  selectedKey: string | null;
  onSelect: (key: string) => void;
  onOpenSolutionDesign: (key: string) => void;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[360px_1fr] lg:items-start">
      <RequirementList selectedKey={selectedKey} onSelect={onSelect} />
      <div>
        {selectedKey === null ? (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground" data-testid="ux-requirement-none-selected">
              左の一覧から Requirement を選択してください。
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-6">
              <RequirementDetail
                key={selectedKey}
                requirementKey={selectedKey}
                onOpenSolutionDesign={onOpenSolutionDesign}
              />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
