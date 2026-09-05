// Issue #409: Journey list -> Journey detail (Step sequence) -> the
// Requirements linked to a selected Step. Levels 1-3 of §4.2's four-level
// disclosure; level 4 (adopted Solution Design) is reached by navigating
// into the Requirement tab (`onOpenRequirement`), never rendered inline here
// -- a Requirement can be satisfied by more than one Solution Design, so
// "the" adopted design is not a property of a Journey Step.

import { useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { JourneyRevisionHistoryCard } from "./revision-history";
import {
  useUxJourneys, useUxJourneyDetail, useUxJourneyBaselineDiff,
  useCreateUxJourney, useAddUxJourneyRevision, useAddUxJourneyUpstreamRef,
  useUxRequirementDetailsBatch, useUxRequirements,
} from "@/api/hooks";
import type {
  UxEvidenceSourceKind, UxJourneyBaselineMode, UxJourneyPerspective,
  UxJourneyStepInput, UxJourneyStepOut, UxJourneyUpstreamRefOut, UxRefKind,
} from "@/api/types";
import {
  DESIGN_STATUS_LABEL, DESIGN_RECHECK_STATE_LABEL, JOURNEY_BASELINE_MODE_LABEL,
  JOURNEY_BASELINE_STATE_LABEL, JOURNEY_PERSPECTIVE_LABEL, EVIDENCE_SOURCE_LABEL,
  DIFF_CHANGE_KIND_LABEL, DIFF_STATE_LABEL, REF_KIND_LABEL, REF_RECHECK_STATE_LABEL,
  REF_RELATION_STATUS_LABEL, REF_TARGET_RESOLUTION_LABEL,
  baselineDiffUnavailableMessage, requirementsForStep, sortJourneys, stepsInOrder,
} from "./model";
import {
  ArtifactReferencesCard, DegradedNote, DesignDecisionControls, EmptyNote,
  LoadErrorCard, LoadingBlock, SectionHeading, StateBadge,
} from "./shared";
import { useUiDraftSource } from "@/lib/ui-draft";

const PERSPECTIVES: UxJourneyPerspective[] = ["as_is", "to_be"];
const BASELINE_MODES: UxJourneyBaselineMode[] = ["linked", "greenfield", "undecided"];
const EVIDENCE_KINDS: UxEvidenceSourceKind[] = [
  "runtime_trace", "human_report", "external_analytics", "none",
];
// Epic #427 §7.1 widened `ux_journey_upstream_ref.ref_kind` with the three
// Product-layer kinds -- and §5.11 then made that table the ONE home of a
// Gap's Journey connection -- but this selector kept the pre-#427 list, so
// the canonical link could not be written from anywhere in the Dashboard.
const REF_KINDS: UxRefKind[] = [
  "purpose_element", "purpose_relation", "capability_entity",
  "product_objective", "product_milestone", "product_gap",
];

function JourneyCreateForm() {
  const create = useCreateUxJourney();
  const [key, setKey] = useState("");
  const [perspective, setPerspective] = useState<UxJourneyPerspective>("to_be");
  const [baselineMode, setBaselineMode] = useState<UxJourneyBaselineMode>("undecided");

  function submit() {
    create.mutate(
      {
        journey_key: key.trim(),
        perspective,
        baseline_mode: perspective === "to_be" ? baselineMode : undefined,
      },
      {
        onSuccess: () => {
          setKey("");
          toast.success("Journey を作成しました");
        },
        onError: (error) => toast.error((error as ApiError).detail || "作成できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-journey-create-form">
      <Input
        placeholder="journey_key(例: checkout-to-be)"
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />
      <div className="flex gap-2">
        <Select value={perspective} onChange={(e) => setPerspective(e.target.value as UxJourneyPerspective)}>
          {PERSPECTIVES.map((p) => (
            <option key={p} value={p}>
              {JOURNEY_PERSPECTIVE_LABEL[p]}
            </option>
          ))}
        </Select>
        {perspective === "to_be" && (
          <Select value={baselineMode} onChange={(e) => setBaselineMode(e.target.value as UxJourneyBaselineMode)}>
            {BASELINE_MODES.map((m) => (
              <option key={m} value={m}>
                {JOURNEY_BASELINE_MODE_LABEL[m]}
              </option>
            ))}
          </Select>
        )}
      </div>
      <Button size="sm" disabled={!key.trim() || create.isPending} onClick={submit}>
        {create.isPending ? "作成中…" : "Journey を作成する"}
      </Button>
    </div>
  );
}

function JourneyList({
  selectedKey,
  onSelect,
}: {
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const journeys = useUxJourneys();

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">UX Journey 一覧</CardTitle>
        <CardDescription>現状(as-is)と目標(to-be)の利用経路。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <JourneyCreateForm />
        {journeys.isLoading ? (
          <LoadingBlock testId="ux-journey-list-loading" />
        ) : journeys.isError ? (
          <LoadErrorCard onRetry={() => journeys.refetch()} testId="ux-journey-list-error" />
        ) : (journeys.data?.journeys ?? []).length === 0 ? (
          <EmptyNote testId="ux-journey-list-empty">この System にはまだ Journey がありません。</EmptyNote>
        ) : (
          <>
            <DegradedNote
              sections={journeys.data?.degraded_sections ?? []}
              detail={journeys.data?.degraded_detail ?? {}}
            />
            <ul className="space-y-1" data-testid="ux-journey-list" data-help-id="ux-design-studio.journey.list">
              {sortJourneys(journeys.data?.journeys ?? []).map((j) => (
                <li key={j.id}>
                  <button
                    type="button"
                    data-testid={`ux-journey-item-${j.journey_key}`}
                    onClick={() => onSelect(j.journey_key)}
                    className={`w-full rounded border p-2 text-left text-sm ${
                      j.journey_key === selectedKey ? "border-primary" : ""
                    }`}
                  >
                    <div className="font-mono">{j.journey_key}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <StateBadge label={JOURNEY_PERSPECTIVE_LABEL[j.perspective]} />
                      <StateBadge
                        label={DESIGN_STATUS_LABEL[j.design_status]}
                        tone={j.design_status === "confirmed" ? "success" : "outline"}
                      />
                      {j.recheck_state === "stale" && (
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

function emptyStepInput(order: number): UxJourneyStepInput {
  return {
    step_key: "", step_order: order, user_intent: "", system_response: "",
    success_criteria: "", failure_mode: "", recovery_path: "", evidence_expectation: "",
    evidence_source_kind: "none",
  };
}

/** Issue #445: a headless registration for one step row's `ux_journey_step`
 * UI draft. A separate component (not a hook call inside the `.map()` below)
 * because the number of steps can change between renders of the SAME
 * `JourneyRevisionForm` instance, and React forbids a hook whose call COUNT
 * varies within one component instance. */
function StepDraftSource({
  journeyKey,
  step,
  seed,
}: {
  journeyKey: string;
  step: UxJourneyStepInput;
  seed?: UxJourneyStepInput;
}) {
  const fields = [
    { fieldName: "user_intent", value: step.user_intent ?? "", dirty: (step.user_intent ?? "") !== (seed?.user_intent ?? ""), validationError: "" },
    { fieldName: "system_response", value: step.system_response ?? "", dirty: (step.system_response ?? "") !== (seed?.system_response ?? ""), validationError: "" },
    { fieldName: "success_criteria", value: step.success_criteria ?? "", dirty: (step.success_criteria ?? "") !== (seed?.success_criteria ?? ""), validationError: "" },
    { fieldName: "failure_mode", value: step.failure_mode ?? "", dirty: (step.failure_mode ?? "") !== (seed?.failure_mode ?? ""), validationError: "" },
    { fieldName: "recovery_path", value: step.recovery_path ?? "", dirty: (step.recovery_path ?? "") !== (seed?.recovery_path ?? ""), validationError: "" },
    { fieldName: "evidence_expectation", value: step.evidence_expectation ?? "", dirty: (step.evidence_expectation ?? "") !== (seed?.evidence_expectation ?? ""), validationError: "" },
  ];
  useUiDraftSource(
    "ux_journey_step.revision",
    // "" (blank step_key -- a newly added, not-yet-keyed row) is refused by
    // the registry as a no-op registration (`src/lib/ui-draft.ts`): there is
    // no discussion thread an unkeyed step could ever be about.
    step.step_key ? `${journeyKey}#${step.step_key}` : "",
    () => ({
      fields,
      selectedItemRef: "",
      activeTab: "",
      comparisonTarget: "",
      localRevisionToken: JSON.stringify(fields.map((f) => [f.fieldName, f.value])),
    }),
  );
  return null;
}

/** Adding a revision replaces the WHOLE step sequence (§2.3: Step は Journey
 * revision の内容であり、独自の revision 鎖を持たない). The form therefore
 * seeds from the current revision's steps when one exists, so revising reads
 * as "edit the sequence" rather than "retype everything". */
function JourneyRevisionForm({ journeyKey, onDone }: { journeyKey: string; onDone: () => void }) {
  const detail = useUxJourneyDetail(journeyKey);
  const addRevision = useAddUxJourneyRevision(journeyKey);
  const seedSteps = stepsInOrder(detail.data?.current_revision).map((s) => ({
    step_key: s.step_key, step_order: s.step_order, user_intent: s.user_intent,
    system_response: s.system_response, success_criteria: s.success_criteria,
    failure_mode: s.failure_mode, recovery_path: s.recovery_path,
    evidence_expectation: s.evidence_expectation, evidence_source_kind: s.evidence_source_kind,
  }));
  // Issue #445: frozen at mount (like `useState`'s own initial value) so a
  // later refetch (e.g. after this same form's own successful submit) does
  // not retroactively change what counts as "unedited" mid-session.
  const seedRef = useRef({
    title: detail.data?.current_revision?.title ?? "",
    beneficiary: detail.data?.current_revision?.beneficiary ?? "",
    usageContext: detail.data?.current_revision?.usage_context ?? "",
    entryTrigger: detail.data?.current_revision?.entry_trigger ?? "",
    valueArrival: detail.data?.current_revision?.value_arrival ?? "",
    summary: detail.data?.current_revision?.summary ?? "",
    stepsByKey: new Map(seedSteps.map((s) => [s.step_key, s])),
  });
  const seed = seedRef.current;
  const [title, setTitle] = useState(detail.data?.current_revision?.title ?? "");
  const [beneficiary, setBeneficiary] = useState(detail.data?.current_revision?.beneficiary ?? "");
  const [usageContext, setUsageContext] = useState(detail.data?.current_revision?.usage_context ?? "");
  const [entryTrigger, setEntryTrigger] = useState(detail.data?.current_revision?.entry_trigger ?? "");
  const [valueArrival, setValueArrival] = useState(detail.data?.current_revision?.value_arrival ?? "");
  const [summary, setSummary] = useState(detail.data?.current_revision?.summary ?? "");
  const [changeNote, setChangeNote] = useState("");
  const [steps, setSteps] = useState<UxJourneyStepInput[]>(seedSteps.length > 0 ? seedSteps : [emptyStepInput(1)]);

  // Issue #445 (§2.2/§2.3): the `ux_journey` draft -- title/beneficiary/...
  // only, the SAME allowlist `app/discussion_adapters.py` registers.
  const journeyFields = [
    { fieldName: "title", value: title, dirty: title !== seed.title, validationError: "" },
    { fieldName: "beneficiary", value: beneficiary, dirty: beneficiary !== seed.beneficiary, validationError: "" },
    { fieldName: "usage_context", value: usageContext, dirty: usageContext !== seed.usageContext, validationError: "" },
    { fieldName: "entry_trigger", value: entryTrigger, dirty: entryTrigger !== seed.entryTrigger, validationError: "" },
    { fieldName: "value_arrival", value: valueArrival, dirty: valueArrival !== seed.valueArrival, validationError: "" },
    { fieldName: "summary", value: summary, dirty: summary !== seed.summary, validationError: "" },
  ];
  useUiDraftSource("ux_journey.revision", journeyKey, () => ({
    fields: journeyFields,
    selectedItemRef: "",
    activeTab: "",
    comparisonTarget: "",
    localRevisionToken: JSON.stringify(journeyFields.map((f) => [f.fieldName, f.value])),
  }));

  function updateStep(i: number, patch: Partial<UxJourneyStepInput>) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
  }

  function submit() {
    addRevision.mutate(
      {
        title, beneficiary, usage_context: usageContext, entry_trigger: entryTrigger,
        value_arrival: valueArrival, summary, change_note: changeNote, steps,
      },
      {
        onSuccess: () => {
          toast.success("Journey の版を追加しました");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-3 rounded border p-3" data-testid="ux-journey-revision-form">
      <div className="grid gap-2 sm:grid-cols-2">
        <Input placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Input placeholder="対象者" value={beneficiary} onChange={(e) => setBeneficiary(e.target.value)} />
        <Input placeholder="文脈" value={usageContext} onChange={(e) => setUsageContext(e.target.value)} />
        <Input placeholder="トリガー" value={entryTrigger} onChange={(e) => setEntryTrigger(e.target.value)} />
        <Input placeholder="価値到達" value={valueArrival} onChange={(e) => setValueArrival(e.target.value)} />
        <Input placeholder="変更メモ(任意)" value={changeNote} onChange={(e) => setChangeNote(e.target.value)} />
      </div>
      <Textarea placeholder="概要" value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} />

      <div className="space-y-2">
        <SectionHeading as="h4">Step 列</SectionHeading>
        {steps.map((s, i) => (
          <div key={i} className="space-y-1 rounded border p-2 text-xs" data-testid={`ux-journey-step-row-${i}`}>
            <StepDraftSource journeyKey={journeyKey} step={s} seed={seed.stepsByKey.get(s.step_key)} />
            <div className="flex gap-2">
              <Input
                placeholder="step_key"
                value={s.step_key}
                onChange={(e) => updateStep(i, { step_key: e.target.value })}
              />
              <Select
                value={s.evidence_source_kind}
                onChange={(e) => updateStep(i, { evidence_source_kind: e.target.value as UxEvidenceSourceKind })}
              >
                {EVIDENCE_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </Select>
            </div>
            <Input
              placeholder="利用者の意図(user_intent)"
              value={s.user_intent}
              onChange={(e) => updateStep(i, { user_intent: e.target.value })}
            />
            <Input
              placeholder="システムの応答(system_response)"
              value={s.system_response}
              onChange={(e) => updateStep(i, { system_response: e.target.value })}
            />
            <Input
              placeholder="成功の基準(success_criteria)"
              value={s.success_criteria}
              onChange={(e) => updateStep(i, { success_criteria: e.target.value })}
            />
            <Input
              placeholder="失敗のしかた(failure_mode)"
              value={s.failure_mode}
              onChange={(e) => updateStep(i, { failure_mode: e.target.value })}
            />
            <Input
              placeholder="回復手段(recovery_path)"
              value={s.recovery_path}
              onChange={(e) => updateStep(i, { recovery_path: e.target.value })}
            />
            <Input
              placeholder="確認できれば成功と言える内容(evidence_expectation)"
              value={s.evidence_expectation}
              onChange={(e) => updateStep(i, { evidence_expectation: e.target.value })}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSteps((prev) => prev.filter((_, idx) => idx !== i))}
            >
              この Step を削除
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setSteps((prev) => [...prev, emptyStepInput(prev.length + 1)])}
        >
          Step を追加
        </Button>
      </div>

      <div className="flex gap-2">
        <Button size="sm" disabled={addRevision.isPending} onClick={submit}>
          {addRevision.isPending ? "保存中…" : "版を保存する"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          キャンセル
        </Button>
      </div>
    </div>
  );
}

function BaselineDiffCard({ journeyKey, baselineMode }: { journeyKey: string; baselineMode: UxJourneyBaselineMode }) {
  const diff = useUxJourneyBaselineDiff(journeyKey);

  return (
    <div className="space-y-2" data-testid="ux-journey-baseline-diff" data-help-id="ux-design-studio.journey.baseline">
      <SectionHeading>現状(as-is)との比較</SectionHeading>
      {diff.isLoading ? (
        <LoadingBlock testId="ux-journey-baseline-diff-loading" />
      ) : diff.isError || !diff.data ? (
        <LoadErrorCard onRetry={() => diff.refetch()} testId="ux-journey-baseline-diff-error" />
      ) : diff.data.diff_state === "not_applicable" ? (
        <EmptyNote testId="ux-journey-baseline-diff-not-applicable">
          {baselineDiffUnavailableMessage(baselineMode)}
        </EmptyNote>
      ) : diff.data.diff_state === "unavailable" ? (
        <EmptyNote testId="ux-journey-baseline-diff-unavailable">{DIFF_STATE_LABEL.unavailable}</EmptyNote>
      ) : diff.data.steps.length === 0 ? (
        <EmptyNote>比較できる Step がありません。</EmptyNote>
      ) : (
        <ul className="space-y-1 text-xs">
          {diff.data.steps.map((entry) => (
            <li key={entry.step_key} className="rounded border p-2">
              <StateBadge
                label={DIFF_CHANGE_KIND_LABEL[entry.change_kind]}
                tone={entry.change_kind === "changed" ? "warning" : entry.change_kind === "unchanged" ? "outline" : "secondary"}
              />
              <span className="ml-2 font-mono">{entry.step_key}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UpstreamRefsCard({ journeyKey, refs }: { journeyKey: string; refs: UxJourneyUpstreamRefOut[] }) {
  const addRef = useAddUxJourneyUpstreamRef(journeyKey);
  const [open, setOpen] = useState(false);
  const [refKind, setRefKind] = useState<UxRefKind>("capability_entity");
  const [targetRef, setTargetRef] = useState("");
  const [note, setNote] = useState("");

  function submit() {
    addRef.mutate(
      { ref_kind: refKind, target_ref: targetRef.trim(), note },
      {
        onSuccess: () => {
          setTargetRef("");
          setNote("");
          setOpen(false);
          toast.success("参照を追加しました");
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-journey-upstream-refs">
      <SectionHeading>Purpose / Capability / Objective への参照</SectionHeading>
      {refs.length === 0 ? (
        <EmptyNote>参照はまだありません。</EmptyNote>
      ) : (
        <ul className="space-y-1 text-xs">
          {refs.map((r) => (
            <li key={r.id} className="rounded border p-2">
              <div className="flex flex-wrap gap-1">
                <StateBadge label={REF_KIND_LABEL[r.ref_kind]} />
                <StateBadge label={REF_RELATION_STATUS_LABEL[r.relation_status]} />
                <StateBadge
                  label={REF_TARGET_RESOLUTION_LABEL[r.target_resolution]}
                  tone={r.target_resolution === "resolved" ? "success" : "destructive"}
                />
                <StateBadge
                  label={REF_RECHECK_STATE_LABEL[r.recheck_state]}
                  tone={r.recheck_state === "current" ? "outline" : "warning"}
                />
              </div>
              <div className="mt-1 font-mono">{r.target_name ?? r.target_ref}</div>
              {/* target_state carries the target's OWN vocabulary verbatim
                  (§2.7 superset rule) -- shown untranslated on purpose. */}
              <div className="text-muted-foreground">target_state: {r.target_state}</div>
            </li>
          ))}
        </ul>
      )}
      <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)}>
        {open ? "追加を閉じる" : "参照を追加する"}
      </Button>
      {open && (
        <div className="space-y-2 rounded border p-2">
          <Select value={refKind} onChange={(e) => setRefKind(e.target.value as UxRefKind)}>
            {REF_KINDS.map((k) => (
              <option key={k} value={k}>
                {REF_KIND_LABEL[k]}
              </option>
            ))}
          </Select>
          <Input
            placeholder="target_ref(例: Capability の id、objective_key / milestone_key / gap_key、または要素の stable id)"
            value={targetRef}
            onChange={(e) => setTargetRef(e.target.value)}
          />
          <Input placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button size="sm" disabled={!targetRef.trim() || addRef.isPending} onClick={submit}>
            {addRef.isPending ? "追加中…" : "追加する"}
          </Button>
        </div>
      )}
    </div>
  );
}

function StepDetail({
  journeyId,
  step,
  onOpenRequirement,
}: {
  journeyId: number;
  step: UxJourneyStepOut;
  onOpenRequirement: (key: string) => void;
}) {
  const requirements = useUxRequirements();
  const allKeys = (requirements.data?.requirements ?? []).map((r) => r.requirement_key);
  const details = useUxRequirementDetailsBatch(allKeys);
  const loadedDetails = details.filter((d) => d.data).map((d) => d.data!);
  const anyLoading = requirements.isLoading || details.some((d) => d.isLoading);
  const anyError = requirements.isError || details.some((d) => d.isError);
  const linked = requirementsForStep(loadedDetails, journeyId, step.step_key);

  return (
    <div className="space-y-3 rounded border p-3" data-testid={`ux-journey-step-detail-${step.step_key}`}>
      <div>
        <div className="text-sm font-medium">{step.user_intent || "(user_intent 未記入)"}</div>
        <dl className="mt-2 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
          <div><dt className="font-medium text-foreground">システムの応答</dt><dd>{step.system_response || "—"}</dd></div>
          <div><dt className="font-medium text-foreground">成功の基準</dt><dd>{step.success_criteria || "—"}</dd></div>
          <div><dt className="font-medium text-foreground">失敗のしかた</dt><dd>{step.failure_mode || "—"}</dd></div>
          <div><dt className="font-medium text-foreground">回復手段</dt><dd>{step.recovery_path || "—"}</dd></div>
        </dl>
        <div className="mt-2 text-xs">
          {/* §0 invariant 6 / §4: runtime_trace is an EXPECTATION, never a
              rendered success -- `EVIDENCE_SOURCE_LABEL.runtime_trace` says
              "未確認" explicitly rather than describing an observation. */}
          <StateBadge
            label={EVIDENCE_SOURCE_LABEL[step.evidence_source_kind]}
            tone={step.evidence_source_kind === "runtime_trace" ? "warning" : "outline"}
          />
          {step.evidence_expectation && (
            <p className="mt-1 text-muted-foreground">{step.evidence_expectation}</p>
          )}
        </div>
      </div>

      <div>
        <SectionHeading as="h4">この Step に紐づく Requirement</SectionHeading>
        {anyLoading ? (
          <LoadingBlock testId="ux-journey-step-requirements-loading" />
        ) : anyError ? (
          <EmptyNote testId="ux-journey-step-requirements-unavailable">
            Requirement の一部を取得できなかったため、紐づきを完全には表示できません。
          </EmptyNote>
        ) : linked.length === 0 ? (
          <EmptyNote testId="ux-journey-step-requirements-empty">
            この Step にはまだ Requirement が紐づいていません。
          </EmptyNote>
        ) : (
          <ul className="space-y-1 text-xs" data-testid="ux-journey-step-requirements">
            {linked.map(({ requirement }) => (
              <li key={requirement.id}>
                <button
                  type="button"
                  className="w-full rounded border p-2 text-left hover:bg-accent"
                  onClick={() => onOpenRequirement(requirement.requirement_key)}
                  data-testid={`ux-journey-step-requirement-link-${requirement.requirement_key}`}
                >
                  <div className="font-mono">{requirement.requirement_key}</div>
                  <div className="mt-1">
                    <StateBadge label={DESIGN_STATUS_LABEL[requirement.design_status]} />
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function JourneyDetail({
  journeyKey,
  onOpenRequirement,
}: {
  journeyKey: string;
  onOpenRequirement: (key: string) => void;
}) {
  const detail = useUxJourneyDetail(journeyKey);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [stepKey, setStepKey] = useState<string | null>(null);

  if (detail.isLoading) return <LoadingBlock testId="ux-journey-detail-loading" />;
  if (detail.isError || !detail.data) {
    return <LoadErrorCard onRetry={() => detail.refetch()} testId="ux-journey-detail-error" />;
  }
  const j = detail.data;
  const steps = stepsInOrder(j.current_revision);

  return (
    <div className="space-y-4" data-testid="ux-journey-detail" data-help-id="ux-design-studio.journey.detail">
      <DegradedNote sections={j.degraded_sections} detail={j.degraded_detail} />
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-mono text-base font-semibold">{j.journey_key}</h2>
        <StateBadge label={JOURNEY_PERSPECTIVE_LABEL[j.perspective]} />
        <StateBadge
          label={DESIGN_STATUS_LABEL[j.design_status]}
          tone={j.design_status === "confirmed" ? "success" : "outline"}
        />
        <StateBadge
          label={DESIGN_RECHECK_STATE_LABEL[j.recheck_state]}
          tone={j.recheck_state === "stale" ? "warning" : "outline"}
        />
      </div>
      <div className="text-xs text-muted-foreground" data-testid="ux-journey-baseline-state">
        {JOURNEY_BASELINE_STATE_LABEL[j.baseline_state]}
      </div>

      {j.current_revision ? (
        <div className="rounded border p-3 text-sm" data-testid="ux-journey-current-revision">
          <div className="font-medium">{j.current_revision.title || "(タイトル未記入)"}</div>
          <p className="mt-1 text-muted-foreground">{j.current_revision.summary}</p>
        </div>
      ) : (
        <EmptyNote testId="ux-journey-no-revision">まだ版がありません。版を追加してください。</EmptyNote>
      )}
      <Button variant="outline" size="sm" onClick={() => setRevisionOpen((v) => !v)}>
        {revisionOpen ? "版の追加を閉じる" : j.current_revision ? "版を追加する" : "最初の版を作成する"}
      </Button>
      {revisionOpen && (
        <JourneyRevisionForm journeyKey={journeyKey} onDone={() => setRevisionOpen(false)} />
      )}

      <div>
        <SectionHeading as="h3">Step 列</SectionHeading>
        {steps.length === 0 ? (
          <EmptyNote>Step はまだありません。</EmptyNote>
        ) : (
          <ul className="mt-2 space-y-1" data-testid="ux-journey-steps">
            {steps.map((s) => (
              <li key={s.step_key}>
                <button
                  type="button"
                  data-testid={`ux-journey-step-item-${s.step_key}`}
                  onClick={() => setStepKey(s.step_key === stepKey ? null : s.step_key)}
                  className={`w-full rounded border p-2 text-left text-xs ${
                    s.step_key === stepKey ? "border-primary" : ""
                  }`}
                >
                  <span className="font-mono">#{s.step_order}</span> {s.user_intent || "(user_intent 未記入)"}
                </button>
              </li>
            ))}
          </ul>
        )}
        {stepKey && (() => {
          const step = steps.find((s) => s.step_key === stepKey);
          return step ? (
            <div className="mt-2">
              <StepDetail journeyId={j.id} step={step} onOpenRequirement={onOpenRequirement} />
            </div>
          ) : null;
        })()}
      </div>

      {j.perspective === "to_be" && <BaselineDiffCard journeyKey={journeyKey} baselineMode={j.baseline_mode} />}
      <JourneyRevisionHistoryCard journeyKey={journeyKey} />

      <UpstreamRefsCard journeyKey={journeyKey} refs={j.upstream_refs} />

      <ArtifactReferencesCard subjectKind="journey" subjectKey={journeyKey} artifacts={j.artifact_references} />

      <DesignDecisionControls
        subjectKind="journey"
        subjectKey={journeyKey}
        capturedDigest={j.current_revision?.content_digest ?? ""}
        decisions={j.decisions}
      />
    </div>
  );
}

export function JourneyPanel({
  selectedKey,
  onSelect,
  onOpenRequirement,
}: {
  selectedKey: string | null;
  onSelect: (key: string) => void;
  onOpenRequirement: (key: string) => void;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[360px_1fr] lg:items-start">
      <JourneyList selectedKey={selectedKey} onSelect={onSelect} />
      <div>
        {selectedKey === null ? (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground" data-testid="ux-journey-none-selected">
              左の一覧から Journey を選択してください。
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-6">
              <JourneyDetail
                key={selectedKey}
                journeyKey={selectedKey}
                onOpenRequirement={onOpenRequirement}
              />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
