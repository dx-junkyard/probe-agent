// Issue #409: Requirement list -> Requirement detail (statement, acceptance
// criteria, the Journey Steps it satisfies) -> the Solution Designs that
// target it (level 4's entry point; the adopted one's handoff view lives in
// the Solution Design tab, reached via `onOpenSolutionDesign`).

import { RequirementRevisionHistoryCard } from "./revision-history";
import { useRef, useState } from "react";
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
  useAddProductFeatureRequirementLink,
} from "@/api/hooks";
import type {
  UxAcceptanceCriterionInput, UxRequirementKind, UxVerificationMethod,
} from "@/api/types";
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
  LoadErrorCard, LoadingBlock, SectionHeading, StateBadge,
} from "./shared";
import { useUiDraftSource } from "@/lib/ui-draft";

const REQUIREMENT_KINDS: UxRequirementKind[] = ["functional", "non_functional", "constraint", "out_of_scope"];
const VERIFICATION_METHODS: UxVerificationMethod[] = [
  "manual_review", "replay", "experiment", "runtime_observation", "not_verifiable",
];

function RequirementCreateForm() {
  const create = useCreateUxRequirement();
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<UxRequirementKind>("functional");

  function submit() {
    create.mutate(
      { requirement_key: key.trim(), requirement_kind: kind },
      {
        onSuccess: () => {
          setKey("");
          toast.success("Requirement を作成しました");
        },
        onError: (error) => toast.error((error as ApiError).detail || "作成できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-requirement-create-form">
      <Input
        placeholder="requirement_key(例: checkout-single-page)"
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />
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
        <RequirementCreateForm />
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
  const detail = useUxRequirementDetail(requirementKey);
  const addRevision = useAddUxRequirementRevision(requirementKey);
  const current = detail.data?.current_revision ?? null;
  // Issue #445: frozen at mount, like `JourneyRevisionForm`'s seed -- a
  // refetch after this form's own submit must not retroactively change what
  // counts as "unedited" mid-session.
  const seedRef = useRef({
    statement: current?.statement ?? "",
    rationale: current?.rationale ?? "",
    constraintText: current?.constraint_text ?? "",
    outOfScopeNote: current?.out_of_scope_note ?? "",
  });
  const seed = seedRef.current;
  const [statement, setStatement] = useState(current?.statement ?? "");
  const [rationale, setRationale] = useState(current?.rationale ?? "");
  const [constraintText, setConstraintText] = useState(current?.constraint_text ?? "");
  const [outOfScopeNote, setOutOfScopeNote] = useState(current?.out_of_scope_note ?? "");
  const [changeNote, setChangeNote] = useState("");

  // Issue #445 (§2.2/§2.3): the `ux_requirement` draft. Acceptance criteria
  // are a #448 (ChildSpec) concern, not a top-level field here.
  const requirementFields = [
    { fieldName: "statement", value: statement, dirty: statement !== seed.statement, validationError: "" },
    { fieldName: "rationale", value: rationale, dirty: rationale !== seed.rationale, validationError: "" },
    { fieldName: "constraint_text", value: constraintText, dirty: constraintText !== seed.constraintText, validationError: "" },
    { fieldName: "out_of_scope_note", value: outOfScopeNote, dirty: outOfScopeNote !== seed.outOfScopeNote, validationError: "" },
  ];
  useUiDraftSource("ux_requirement.revision", requirementKey, () => ({
    fields: requirementFields,
    selectedItemRef: "",
    activeTab: "",
    comparisonTarget: "",
    localRevisionToken: JSON.stringify(requirementFields.map((f) => [f.fieldName, f.value])),
  }));
  const seedCriteria = (current?.acceptance_criteria ?? []).map((c) => ({
    criterion_key: c.criterion_key, criterion_order: c.criterion_order, statement: c.statement,
    verification_method: c.verification_method, verification_note: c.verification_note,
  }));
  const [criteria, setCriteria] = useState<UxAcceptanceCriterionInput[]>(
    seedCriteria.length > 0 ? seedCriteria : [],
  );

  function updateCriterion(i: number, patch: Partial<UxAcceptanceCriterionInput>) {
    setCriteria((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }

  function submit() {
    addRevision.mutate(
      {
        statement, rationale, constraint_text: constraintText, out_of_scope_note: outOfScopeNote,
        change_note: changeNote, acceptance_criteria: criteria,
      },
      {
        onSuccess: () => {
          toast.success("Requirement の版を追加しました");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-3 rounded border p-3" data-testid="ux-requirement-revision-form">
      <Textarea placeholder="要件の文" value={statement} onChange={(e) => setStatement(e.target.value)} rows={2} />
      <Textarea placeholder="理由(rationale)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <Input placeholder="制約(constraint_text)" value={constraintText} onChange={(e) => setConstraintText(e.target.value)} />
      <Input
        placeholder="対象外にした理由(out_of_scope_note)"
        value={outOfScopeNote}
        onChange={(e) => setOutOfScopeNote(e.target.value)}
      />
      <Input placeholder="変更メモ(任意)" value={changeNote} onChange={(e) => setChangeNote(e.target.value)} />

      <div className="space-y-2">
        <SectionHeading as="h4">受入条件</SectionHeading>
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

function StepLinkForm({ requirementKey, onDone }: { requirementKey: string; onDone: () => void }) {
  const journeys = useUxJourneys();
  const [journeyKey, setJourneyKey] = useState("");
  const journeyDetail = useUxJourneyDetail(journeyKey || null);
  const [stepKey, setStepKey] = useState("");
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
      <Select value={journeyKey} onChange={(e) => { setJourneyKey(e.target.value); setStepKey(""); }}>
        <option value="">Journey を選択</option>
        {(journeys.data?.journeys ?? []).map((j) => (
          <option key={j.journey_key} value={j.journey_key}>
            {j.journey_key}
          </option>
        ))}
      </Select>
      {journeyKey && (
        <Select value={stepKey} onChange={(e) => setStepKey(e.target.value)}>
          <option value="">Step を選択</option>
          {steps.map((s) => (
            <option key={s.step_key} value={s.step_key}>
              {s.step_key}: {s.user_intent}
            </option>
          ))}
        </Select>
      )}
      <Input placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" disabled={!journeyKey || !stepKey || addLink.isPending} onClick={submit}>
        {addLink.isPending ? "追加中…" : "紐づける"}
      </Button>
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
  const [revisionOpen, setRevisionOpen] = useState(false);
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
      <Button variant="outline" size="sm" onClick={() => setRevisionOpen((v) => !v)}>
        {revisionOpen ? "版の追加を閉じる" : r.current_revision ? "版を追加する" : "最初の版を作成する"}
      </Button>
      {revisionOpen && (
        <RequirementRevisionForm requirementKey={requirementKey} onDone={() => setRevisionOpen(false)} />
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
