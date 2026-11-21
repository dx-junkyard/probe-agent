// Issue #432 (Epic #427): the Gap Workbench lane -- Objective/Milestone
// filtered Gap list, source-kind breakdown, shared-source federation read,
// and the manual actions §9.2 requires (確認/関連付け/保留/解消/reopen/
// 優先バンド設定). Selecting a Gap only changes which one is shown in
// detail -- it never triggers a write on its own (§9.2 non-goal).

import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/api/client";
import { useDirtyGuard } from "./unsaved-work";
import {
  useAddProductGapArtifactLink, useAddProductGapEvidenceRef, useAddProductGapRevision,
  useAddProductGapSourceRef, useCreateProductGap,
  useLinkProductGapToJourney, useProductGapDetail, useRecordProductGapDecision,
  useRecordProductGapResolutionChoice, useUxJourneys,
} from "@/api/hooks";
import type {
  GapWorkbenchEntryOut, GapWorkbenchOut, ObjectiveMapOut,
  ProductDeepLinkState, ProductDeepLinkTargetState,
  ProductGapArtifactLinkKind, ProductGapDetailOut, ProductGapEvidenceKind,
  ProductGapLifecycle, ProductGapPriorityBand, ProductGapResolutionApproach,
  ProductGapRevisionOut, ProductGapSourceKind,
  ProductGapTargetMode,
} from "@/api/types";
import { formatTimestamp } from "@/lib/utils";
import {
  AUTHORSHIP_LABEL, DEEP_LINK_STATE_LABEL, DEEP_LINK_TARGET_STATE_LABEL, GAP_ARTIFACT_LINK_KIND_LABEL,
  GAP_EVIDENCE_KIND_LABEL,
  GAP_DECISION_LABEL, GAP_EFFECTIVE_TARGET_AVAILABILITY_LABEL, GAP_LIFECYCLE_LABEL,
  GAP_PRIORITY_BAND_LABEL, GAP_READ_FLAG_LABEL, GAP_SOURCE_KIND_LABEL,
  GAP_RESOLUTION_APPROACH_LABEL, GAP_SOURCE_STATE_LABEL, RECHECK_STATE_LABEL, sharedGapKeysForSource,
  isStaleDigestErrorCode,
  type GapWorkbenchFilters,
} from "./model";
import { StaleDigestNotice } from "./objective-forms";
import {
  DecisionConfirmDialog, RejectedNotice, rejectionFromError, useDecisionDialog, type Rejection,
} from "./decision-confirm";
import { ReferenceTargetPicker } from "./reference-picker";

const LIFECYCLE_VALUES: ProductGapLifecycle[] = [
  "open", "acknowledged", "deferred", "resolved", "rejected", "obsolete",
];
const PRIORITY_BAND_VALUES: ProductGapPriorityBand[] = ["unset", "watch", "next", "now"];
const ARTIFACT_LINK_KINDS: ProductGapArtifactLinkKind[] = [
  "ux_requirement", "product_feature", "solution_design", "issue_draft",
];
const TARGET_MODE_VALUES: ProductGapTargetMode[] = ["own", "inherited_from_milestone", "unknown"];
const TARGET_MODE_LABEL: Record<ProductGapTargetMode, string> = {
  own: "この Gap 自身の目標状態を書く",
  inherited_from_milestone: "Milestone の目標状態を継承する",
  unknown: "まだ決めていない",
};

export function GapWorkbenchFiltersBar({
  filters, onChange, map,
}: {
  filters: GapWorkbenchFilters;
  onChange: (next: GapWorkbenchFilters) => void;
  map: ObjectiveMapOut | undefined;
}) {
  const objectiveKeys = (map?.nodes ?? []).map((n) => n.objective_key);
  const milestoneKeys = (map?.nodes ?? []).flatMap((n) => n.milestones.map((m) => m.milestone_key));
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="gap-workbench-filters">
      <Select
        aria-label="Objective で絞り込み"
        value={filters.objectiveKey ?? ""}
        onChange={(e) => onChange({ ...filters, objectiveKey: e.target.value || null, milestoneKey: null })}
      >
        <option value="">Objective: すべて</option>
        {objectiveKeys.map((k) => <option key={k} value={k}>{k}</option>)}
      </Select>
      <Select
        aria-label="Milestone で絞り込み"
        value={filters.milestoneKey ?? ""}
        onChange={(e) => onChange({ ...filters, milestoneKey: e.target.value || null })}
      >
        <option value="">Milestone: すべて</option>
        {milestoneKeys.map((k) => <option key={k} value={k}>{k}</option>)}
      </Select>
      <Select
        aria-label="解消状態で絞り込み"
        value={filters.lifecycle ?? ""}
        onChange={(e) => onChange({ ...filters, lifecycle: (e.target.value || null) as ProductGapLifecycle | null })}
      >
        <option value="">解消状態: すべて</option>
        {LIFECYCLE_VALUES.map((v) => <option key={v} value={v}>{GAP_LIFECYCLE_LABEL[v]}</option>)}
      </Select>
      {(filters.objectiveKey || filters.milestoneKey || filters.lifecycle) && (
        <Button variant="ghost" size="sm" onClick={() => onChange({ objectiveKey: null, milestoneKey: null, lifecycle: null })}>
          絞り込みを解除
        </Button>
      )}
    </div>
  );
}

/** §9.2's source-kind breakdown -- a COUNT per kind, never a ranking (§0
 * invariant 7: displayed in the server's own array order). */
export function SourceKindBreakdown({ workbench }: { workbench: GapWorkbenchOut }) {
  if (workbench.source_kind_breakdown.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 text-xs" data-testid="gap-workbench-source-breakdown">
      {workbench.source_kind_breakdown.map((b) => (
        <Badge key={b.source_kind} variant="outline">
          {GAP_SOURCE_KIND_LABEL[b.source_kind]} {b.gap_count}
        </Badge>
      ))}
    </div>
  );
}

export function GapEntryRow({
  entry, selected, onSelect,
}: {
  entry: GapWorkbenchEntryOut;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected}
        className={`w-full rounded border p-2 text-left text-sm hover:bg-muted ${selected ? "border-primary bg-muted" : ""}`}
        data-testid={`gap-entry-${entry.gap_key}`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-medium">{entry.title || entry.gap_key}</span>
          <div className="flex flex-wrap gap-1">
            <Badge variant="outline">{GAP_LIFECYCLE_LABEL[entry.lifecycle]}</Badge>
            {entry.priority_band !== "unset" && <Badge variant="secondary">{GAP_PRIORITY_BAND_LABEL[entry.priority_band]}</Badge>}
          </div>
        </div>
        {entry.milestone_key && (
          <p className="mt-0.5 text-xs text-muted-foreground">Milestone: {entry.milestone_key}</p>
        )}
        {entry.read_flags.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-xs text-amber-700 dark:text-amber-300">
            {entry.read_flags.map((f) => <li key={f}>・{GAP_READ_FLAG_LABEL[f]}</li>)}
          </ul>
        )}
      </button>
    </li>
  );
}

export function GapEntryList({
  entries, selectedGapKey, onSelect,
}: {
  entries: readonly GapWorkbenchEntryOut[];
  selectedGapKey: string | null;
  onSelect: (gapKey: string) => void;
}) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="gap-entry-list-empty">
        この絞り込みに一致する Gap がありません。
      </p>
    );
  }
  return (
    <ul className="space-y-1" data-testid="gap-entry-list">
      {entries.map((e) => (
        <GapEntryRow key={e.gap_key} entry={e} selected={e.gap_key === selectedGapKey} onSelect={() => onSelect(e.gap_key)} />
      ))}
    </ul>
  );
}

/** Every decision on this screen sends `decisionDigest` -- read from the
 * Gap's OWN `decision_digest` field (§B/§10.1), NEVER
 * `current_revision.content_digest`: an `inherited_from_milestone` Gap is
 * judged partly against the Milestone's target, so the two differ and the
 * revision digest would 409 on every decision. A stale-digest 409 renders
 * `StaleDigestNotice` -- reload and re-read, never a silent/automatic
 * retry.
 *
 * UX audit 2026-11-17 D8: each decision goes through the confirmation dialog,
 * which names the Gap and its CURRENT lifecycle (the server's value). There
 * is no client transition table; a refusal is the server's own code, shown
 * in Japanese with the current state alongside. */
function DecisionRationaleControls({
  gap, onStale,
}: {
  gap: ProductGapDetailOut;
  onStale: () => void;
}) {
  const record = useRecordProductGapDecision(gap.gap_key);
  const dialog = useDecisionDialog<"acknowledge" | "defer" | "resolve" | "reopen">();
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [stale, setStale] = useState(false);
  const stateLabel = GAP_LIFECYCLE_LABEL[gap.lifecycle];

  function submit(decision: "acknowledge" | "defer" | "resolve" | "reopen", rationale: string) {
    setRejection(null);
    setStale(false);
    record.mutate(
      { decision, rationale, captured_digest: gap.decision_digest },
      {
        onSuccess: () => {
          dialog.close();
          toast.success(`「${GAP_DECISION_LABEL[decision]}」を記録しました`);
        },
        onError: (error) => {
          dialog.close();
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした", `現在の解消状態: ${stateLabel}`));
        },
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="gap-decision-controls">
      <p className="text-xs" data-testid="gap-decision-current-state">現在の解消状態: {stateLabel}</p>
      <div className="flex flex-wrap gap-2">
        {(["acknowledge", "defer", "resolve", "reopen"] as const).map((d) => (
          <Button key={d} size="sm" variant="outline" disabled={record.isPending} onClick={() => dialog.open(d)} data-testid={`gap-decision-${d}`}>
            {GAP_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} testId="gap-decision-rejected" />}
      {dialog.pendingKind && (
        <DecisionConfirmDialog
          open
          onOpenChange={(open) => { if (!open) dialog.close(); }}
          title={`Gap「${gap.title || gap.gap_key}」を${GAP_DECISION_LABEL[dialog.pendingKind]}`}
          currentStateText={`現在の解消状態: ${stateLabel}`}
          consequence="判断の記録だけを追加します。検出元・Milestone の達成・実装は変わりません。前の判断は履歴に残ります。"
          confirmLabel={GAP_DECISION_LABEL[dialog.pendingKind]}
          pending={record.isPending}
          onConfirm={(reason) => submit(dialog.pendingKind!, reason)}
        />
      )}
    </div>
  );
}

function PrioritizeControls({
  gap, onStale,
}: {
  gap: ProductGapDetailOut;
  onStale: () => void;
}) {
  const record = useRecordProductGapDecision(gap.gap_key);
  const [band, setBand] = useState<ProductGapPriorityBand>(gap.priority_band);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [rejection, setRejection] = useState<Rejection | null>(null);
  useDirtyGuard(`gap-prioritize:${gap.gap_key}`, band !== gap.priority_band);
  const [stale, setStale] = useState(false);

  function submit(rationale: string) {
    setStale(false);
    setRejection(null);
    record.mutate(
      { decision: "prioritize", priority_band: band, rationale, captured_digest: gap.decision_digest },
      {
        onSuccess: () => { setConfirmOpen(false); toast.success("優先バンドを記録しました"); },
        onError: (error) => {
          setConfirmOpen(false);
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection(rejectionFromError(error, "記録できませんでした", `現在の解消状態: ${GAP_LIFECYCLE_LABEL[gap.lifecycle]}`));
        },
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="gap-prioritize-controls">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="優先バンド" value={band} onChange={(e) => setBand(e.target.value as ProductGapPriorityBand)}>
          {PRIORITY_BAND_VALUES.map((v) => <option key={v} value={v}>{GAP_PRIORITY_BAND_LABEL[v]}</option>)}
        </Select>
        <Button size="sm" variant="outline" disabled={record.isPending} onClick={() => setConfirmOpen(true)} data-testid="gap-decision-prioritize">
          {GAP_DECISION_LABEL.prioritize}
        </Button>
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && <RejectedNotice {...rejection} testId="gap-prioritize-rejected" />}
      <DecisionConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Gap「${gap.title || gap.gap_key}」の優先バンドを「${GAP_PRIORITY_BAND_LABEL[band]}」にする`}
        currentStateText={`現在の優先バンド: ${GAP_PRIORITY_BAND_LABEL[gap.priority_band]}`}
        consequence="人が置く優先バンドを記録します。解消状態は変わりません。"
        confirmLabel="記録する"
        pending={record.isPending}
        onConfirm={submit}
      />
    </div>
  );
}

const SOURCE_KINDS: ProductGapSourceKind[] = [
  "manual", "system_understanding_gap", "understanding_review_gap", "understanding_claim_change",
  "functional_lineage_gap", "value_network_notice", "journey_baseline_diff", "requirement_diff",
  "capability_drift", "runtime_alignment_mismatch", "node_anomaly", "joint_understanding_open",
  "inquiry_unresolved", "issue_draft",
];

/** The documented `source_ref` shape for a kind this screen cannot list
 * (§5.4's table). A hint only -- the server is what checks it. */
const SOURCE_REF_FORMAT_HINT: Partial<Record<ProductGapSourceKind, string>> = {
  understanding_review_gap: "gap_type|node_name",
  understanding_claim_change: "section|name",
  journey_baseline_diff: "journey_key|step_key",
  requirement_diff: "requirement_key|criterion_key",
  capability_drift: "path|qualified_name または entrypoint:<entrypoint_id>",
  runtime_alignment_mismatch: "review_subject_id",
  node_anomaly: "node_key|dedupe_key",
  joint_understanding_open: "共同理解セッションの id",
  inquiry_unresolved: "Inquiry の id",
};

/** §5.4: attach a detection source. The kind is one of the 14 finite kinds;
 * its reference comes from that kind's own list where one exists. `manual`
 * has no external source and sends an empty reference. The server resolves
 * the source and captures the pins itself; an unresolvable one is refused
 * (422 `product_gap_source_ref_unresolvable`) and shown as returned. */
function GapSourceRefForm({ gapKey }: { gapKey: string }) {
  const add = useAddProductGapSourceRef(gapKey);
  const [kind, setKind] = useState<ProductGapSourceKind>("functional_lineage_gap");
  const [sourceRef, setSourceRef] = useState("");
  const [note, setNote] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  useDirtyGuard(`gap-source-ref:${gapKey}`, sourceRef !== "" || note !== "");
  const needsRef = kind !== "manual";

  function submit() {
    setRejection(null);
    add.mutate(
      { source_kind: kind, source_ref: needsRef ? sourceRef.trim() : "", note },
      {
        onSuccess: () => { setSourceRef(""); setNote(""); toast.success("検出元を追加しました"); },
        onError: (error) => setRejection(rejectionFromError(error, "検出元を追加できませんでした")),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-source-ref-form">
      <p className="text-xs font-semibold">検出元を追加する</p>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="検出元の種類"
          value={kind}
          onChange={(e) => { setKind(e.target.value as ProductGapSourceKind); setSourceRef(""); }}
          data-testid="gap-source-ref-kind"
        >
          {SOURCE_KINDS.map((k) => <option key={k} value={k}>{GAP_SOURCE_KIND_LABEL[k]}</option>)}
        </Select>
        {needsRef ? (
          <ReferenceTargetPicker
            kind={kind}
            value={sourceRef}
            onChange={setSourceRef}
            ariaLabel="検出元"
            manualPlaceholder={SOURCE_REF_FORMAT_HINT[kind] ? `形式: ${SOURCE_REF_FORMAT_HINT[kind]}` : undefined}
            testId="gap-source-ref-target"
          />
        ) : (
          <p className="text-xs text-muted-foreground" data-testid="gap-source-ref-manual-note">
            手動記録は外部の検出元を持ちません。この Gap を人が見つけたことを記録します。
          </p>
        )}
      </div>
      <Input aria-label="検出元のメモ" placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" variant="outline" disabled={(needsRef && !sourceRef.trim()) || add.isPending} onClick={submit} data-testid="gap-source-ref-submit">
        検出元を追加する
      </Button>
      {rejection && <RejectedNotice {...rejection} testId="gap-source-ref-rejected" />}
    </div>
  );
}

const EVIDENCE_KINDS: ProductGapEvidenceKind[] = [
  "trace", "experiment", "replay_run", "human_report", "external_report", "repository_path", "other",
];
const EVIDENCE_REF_PLACEHOLDER: Record<ProductGapEvidenceKind, string> = {
  trace: "Trace の id",
  experiment: "Experiment の id",
  replay_run: "Replay run の id",
  human_report: "報告の出典(誰が・いつ・どこで)",
  external_report: "外部の報告の URL や名前",
  repository_path: "リポジトリ内のパス",
  other: "参照",
};

/** §5.9: attach evidence as a REFERENCE (no body column exists). Experiment
 * is listable; the rest are typed -- a Trace / Replay run has no System-wide
 * list here, and a report or path is free text by nature. */
function GapEvidenceRefForm({ gapKey }: { gapKey: string }) {
  const add = useAddProductGapEvidenceRef(gapKey);
  const [kind, setKind] = useState<ProductGapEvidenceKind>("human_report");
  const [evidenceRef, setEvidenceRef] = useState("");
  const [note, setNote] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  useDirtyGuard(`gap-evidence-ref:${gapKey}`, evidenceRef !== "" || note !== "");
  const freeText = kind === "human_report" || kind === "external_report" || kind === "repository_path" || kind === "other";

  function submit() {
    setRejection(null);
    add.mutate(
      { evidence_kind: kind, evidence_ref: evidenceRef.trim(), note },
      {
        onSuccess: () => { setEvidenceRef(""); setNote(""); toast.success("証跡を追加しました"); },
        onError: (error) => setRejection(rejectionFromError(error, "証跡を追加できませんでした")),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-evidence-ref-form">
      <p className="text-xs font-semibold">証跡を追加する</p>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="証跡の種類"
          value={kind}
          onChange={(e) => { setKind(e.target.value as ProductGapEvidenceKind); setEvidenceRef(""); }}
          data-testid="gap-evidence-ref-kind"
        >
          {EVIDENCE_KINDS.map((k) => <option key={k} value={k}>{GAP_EVIDENCE_KIND_LABEL[k]}</option>)}
        </Select>
        {freeText ? (
          <Input
            aria-label="証跡の参照"
            placeholder={EVIDENCE_REF_PLACEHOLDER[kind]}
            value={evidenceRef}
            onChange={(e) => setEvidenceRef(e.target.value)}
            className="max-w-xs"
            data-testid="gap-evidence-ref-target"
          />
        ) : (
          <ReferenceTargetPicker
            kind={kind}
            value={evidenceRef}
            onChange={setEvidenceRef}
            ariaLabel="証跡の参照"
            manualPlaceholder={EVIDENCE_REF_PLACEHOLDER[kind]}
            testId="gap-evidence-ref-target"
          />
        )}
      </div>
      <Input aria-label="証跡のメモ" placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" variant="outline" disabled={!evidenceRef.trim() || add.isPending} onClick={submit} data-testid="gap-evidence-ref-submit">
        証跡を追加する
      </Button>
      {rejection && <RejectedNotice {...rejection} testId="gap-evidence-ref-rejected" />}
    </div>
  );
}

const RESOLUTION_APPROACHES: ProductGapResolutionApproach[] = [
  "feature_new", "feature_extend", "operation", "sponsor_decision", "out_of_scope",
];

/** How a human chose to close the Gap -- its own manual-only ledger. It
 * never moves `lifecycle` or `priority_band`, which the dialog says. The
 * server enforces the `feature_key` rule (required for `feature_extend`,
 * optional for `feature_new`, refused otherwise); the Feature field is only
 * shown where the server accepts one. */
function GapResolutionChoiceForm({ gap }: { gap: ProductGapDetailOut }) {
  const record = useRecordProductGapResolutionChoice(gap.gap_key);
  const [approach, setApproach] = useState<ProductGapResolutionApproach>("feature_new");
  const [featureKey, setFeatureKey] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [rejection, setRejection] = useState<Rejection | null>(null);
  useDirtyGuard(`gap-resolution-choice:${gap.gap_key}`, featureKey !== "");
  const takesFeature = approach === "feature_new" || approach === "feature_extend";
  const current = gap.resolution_approach ?? "undecided";

  function submit(rationale: string) {
    setRejection(null);
    record.mutate(
      {
        approach, rationale, captured_digest: gap.decision_digest,
        feature_key: takesFeature && featureKey ? featureKey : null,
      },
      {
        onSuccess: () => { setConfirmOpen(false); setFeatureKey(""); toast.success("解消方針を記録しました"); },
        onError: (error) => { setConfirmOpen(false); setRejection(rejectionFromError(error, "解消方針を記録できませんでした")); },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-resolution-choice-form">
      <p className="text-xs font-semibold">解消方針を決める(人間の判断として記録されます)</p>
      <p className="text-xs" data-testid="gap-resolution-current">
        現在の方針: {GAP_RESOLUTION_APPROACH_LABEL[current]}
        {gap.resolution_feature_key ? `(Feature: ${gap.resolution_feature_key})` : ""}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="解消方針"
          value={approach}
          onChange={(e) => { setApproach(e.target.value as ProductGapResolutionApproach); setFeatureKey(""); }}
          data-testid="gap-resolution-approach"
        >
          {RESOLUTION_APPROACHES.map((a) => <option key={a} value={a}>{GAP_RESOLUTION_APPROACH_LABEL[a]}</option>)}
        </Select>
        {takesFeature && (
          <ReferenceTargetPicker
            kind="product_feature"
            value={featureKey}
            onChange={setFeatureKey}
            ariaLabel="解消に使う Feature"
            testId="gap-resolution-feature"
          />
        )}
      </div>
      {approach === "feature_new" && (
        <p className="text-xs text-muted-foreground">新しい Feature がまだ無い場合は、Feature を選ばずに記録できます。</p>
      )}
      <Button
        size="sm" variant="outline"
        disabled={record.isPending || (approach === "feature_extend" && !featureKey)}
        onClick={() => setConfirmOpen(true)}
        data-testid="gap-resolution-submit"
      >
        解消方針を記録する
      </Button>
      {approach === "feature_extend" && !featureKey && (
        <p className="text-xs text-muted-foreground">既存の Feature を拡張する場合は、拡張する Feature を選んでください。</p>
      )}
      {rejection && <RejectedNotice {...rejection} testId="gap-resolution-rejected" />}
      <DecisionConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Gap「${gap.title || gap.gap_key}」の解消方針を「${GAP_RESOLUTION_APPROACH_LABEL[approach]}」にする`}
        currentStateText={`現在の方針: ${GAP_RESOLUTION_APPROACH_LABEL[current]}`}
        consequence="方針の記録だけを追加します。Gap は解消済みにならず、優先バンドも変わりません。"
        confirmLabel="記録する"
        reasonRequired
        pending={record.isPending}
        onConfirm={submit}
      />
    </div>
  );
}

/** §427 D: creates the Gap identity row under a Milestone. `defaultMilestoneKey`
 * prefills from the current selection/filter (§9.4's deep link), but any
 * Milestone in this Objective Map may be chosen. */
export function CreateGapForm({
  map, defaultMilestoneKey, onCreated,
}: {
  map: ObjectiveMapOut | undefined;
  defaultMilestoneKey: string | null;
  onCreated: (gapKey: string) => void;
}) {
  const create = useCreateProductGap();
  const milestoneKeys = (map?.nodes ?? []).flatMap((n) => n.milestones.map((m) => m.milestone_key));
  const [milestoneKey, setMilestoneKey] = useState(defaultMilestoneKey ?? milestoneKeys[0] ?? "");
  const [gapKey, setGapKey] = useState("");
  const [rejection, setRejection] = useState<{ code: string; message: string } | null>(null);

  // `useState` seeds ONCE, so a default that arrives later never lands: the
  // Objective Map loads async, and the deep link / filter / System can all
  // change afterwards. Two separate ways that produced a wrong owner:
  // creating under whatever Milestone happened to be first when the map was
  // still empty, and -- because these slugs are unique only WITHIN a System
  // (§1's identity rule) -- keeping a same-named key across a System switch,
  // which creates the Gap under a different System's Milestone entirely.
  //
  // Adjusted during render (React's "reacting to a changed input" pattern),
  // the same discipline `objective-tree.tsx` documents: an effect would paint
  // once with the stale Milestone selected. It follows the DEFAULT, not every
  // render -- a Milestone the developer picked here by hand survives until
  // the default itself moves.
  const [lastDefault, setLastDefault] = useState(defaultMilestoneKey);
  if (lastDefault !== defaultMilestoneKey) {
    setLastDefault(defaultMilestoneKey);
    if (defaultMilestoneKey) setMilestoneKey(defaultMilestoneKey);
  }
  // A key that is not in the CURRENT map belongs to a Milestone this screen
  // is not showing (a System switch, or a deleted Milestone). Creating under
  // it would either fail or -- worse -- succeed against something the
  // developer cannot see, so fall back to a Milestone that IS on offer.
  const selectableMilestoneKey = milestoneKeys.includes(milestoneKey)
    ? milestoneKey
    : (defaultMilestoneKey && milestoneKeys.includes(defaultMilestoneKey)
        ? defaultMilestoneKey
        : milestoneKeys[0] ?? "");

  function submit() {
    setRejection(null);
    create.mutate(
      { milestone_key: selectableMilestoneKey, gap_key: gapKey.trim() },
      {
        onSuccess: (out) => { setGapKey(""); toast.success("Gap を作成しました"); onCreated(out.gap_key); },
        onError: (error) => {
          const apiError = error as ApiError;
          setRejection({ code: apiError.code ?? "unknown", message: apiError.detail || "作成できませんでした" });
        },
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="create-gap-form">
      <p className="text-xs font-semibold">Gap を作成する</p>
      <div className="flex flex-wrap gap-2">
        <Select aria-label="所属する Milestone" value={selectableMilestoneKey} onChange={(e) => setMilestoneKey(e.target.value)}>
          {milestoneKeys.length === 0 && <option value="">Milestone がありません</option>}
          {milestoneKeys.map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <Input aria-label="gap_key" placeholder="gap_key" value={gapKey} onChange={(e) => setGapKey(e.target.value)} className="max-w-xs" />
        <Button size="sm" variant="outline" disabled={!selectableMilestoneKey || !gapKey.trim() || create.isPending} onClick={submit} data-testid="create-gap-submit">
          作成する
        </Button>
      </div>
      {rejection && (
        <div className="rounded border border-destructive p-2 text-xs" data-testid="create-gap-rejected">
          <span className="font-mono text-destructive">{rejection.code}</span>
          <span className="ml-2">{rejection.message}</span>
        </div>
      )}
    </div>
  );
}

/** Seeded from the current revision: the endpoint appends a FULL snapshot,
 * so a blank form turns 「1 項目だけ直す」 into 「他を全部空へ戻す」, and
 * makes 「意図的に空にする」 indistinguishable from 「触っていない」.
 * `useState` seeds once per mount, which is correct because the Gap detail
 * pane is remounted per `(system_id, gap_key)`. */
function GapRevisionForm({
  gapKey, current,
}: {
  gapKey: string;
  current: ProductGapRevisionOut | null;
}) {
  const add = useAddProductGapRevision(gapKey);
  const [title, setTitle] = useState(current?.title ?? "");
  const [currentState, setCurrentState] = useState(current?.current_state ?? "");
  const [targetState, setTargetState] = useState(current?.target_state ?? "");
  const [targetStateMode, setTargetStateMode] = useState<ProductGapTargetMode>(
    current?.target_state_mode ?? "unknown",
  );
  const [interpretation, setInterpretation] = useState(current?.interpretation ?? "");
  useDirtyGuard(
    `gap-revision:${gapKey}`,
    title !== (current?.title ?? "")
      || currentState !== (current?.current_state ?? "")
      || targetState !== (current?.target_state ?? "")
      || targetStateMode !== (current?.target_state_mode ?? "unknown")
      || interpretation !== (current?.interpretation ?? ""),
  );

  function submit() {
    add.mutate(
      {
        title, current_state: currentState, target_state: targetState,
        target_state_mode: targetStateMode, interpretation,
      },
      {
        onSuccess: () => toast.success("内容を記録しました"),
        onError: (error) => toast.error((error as ApiError).detail || "記録できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-revision-form">
      <p className="text-xs font-semibold">内容を記録する(新しい版として追記されます)</p>
      <Input aria-label="タイトル" placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea aria-label="現在状態" placeholder="現在状態" value={currentState} onChange={(e) => setCurrentState(e.target.value)} rows={2} />
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="目標状態の扱い" value={targetStateMode} onChange={(e) => setTargetStateMode(e.target.value as ProductGapTargetMode)}>
          {TARGET_MODE_VALUES.map((v) => <option key={v} value={v}>{TARGET_MODE_LABEL[v]}</option>)}
        </Select>
      </div>
      {targetStateMode === "own" && (
        <Textarea aria-label="目標状態" placeholder="目標状態" value={targetState} onChange={(e) => setTargetState(e.target.value)} rows={2} />
      )}
      <Textarea aria-label="解釈" placeholder="解釈" value={interpretation} onChange={(e) => setInterpretation(e.target.value)} rows={2} />
      <Button size="sm" variant="outline" disabled={add.isPending} onClick={submit} data-testid="gap-revision-submit">
        記録する
      </Button>
    </div>
  );
}

/** §5.11/§A: writes the Gap's Journey connection through the Journey's OWN
 * endpoint (`ux_journey_upstream_ref(ref_kind='product_gap')`) -- the one
 * writable home for this relation. Never `product_gap_artifact_link`, which
 * would let the two disagree (the twin-canon this Epic forbids). The
 * server-side reverse lookup this write feeds is not currently exposed on
 * the Gap response, so this screen cannot yet list a Gap's already-linked
 * Journeys -- only add one. */
function JourneyLinkForm({ gapKey }: { gapKey: string }) {
  const journeys = useUxJourneys();
  const link = useLinkProductGapToJourney(gapKey);
  const [journeyKey, setJourneyKey] = useState("");
  const [note, setNote] = useState("");
  useDirtyGuard(`gap-journey-link:${gapKey}`, journeyKey !== "" || note !== "");

  const options = journeys.data?.journeys ?? [];

  function submit() {
    if (!journeyKey) return;
    link.mutate(
      { journeyKey, note },
      {
        onSuccess: () => { setNote(""); toast.success("Journey へ関連付けました"); },
        onError: (error) => toast.error((error as ApiError).detail || "関連付けできませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-journey-link-form">
      <p className="text-xs font-semibold">UX Journey へ関連付ける</p>
      {journeys.isLoading ? (
        <p className="text-xs text-muted-foreground">Journey を読み込んでいます…</p>
      ) : options.length === 0 ? (
        <p className="text-xs text-muted-foreground">この System にはまだ UX Journey がありません。</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Select aria-label="関連付ける Journey" value={journeyKey} onChange={(e) => setJourneyKey(e.target.value)}>
            <option value="">選択してください</option>
            {options.map((j) => <option key={j.journey_key} value={j.journey_key}>{j.title || j.journey_key}</option>)}
          </Select>
          <Input aria-label="関連付けの note" placeholder="note(任意)" value={note} onChange={(e) => setNote(e.target.value)} className="max-w-xs" />
          <Button size="sm" variant="outline" disabled={!journeyKey || link.isPending} onClick={submit} data-testid="gap-journey-link-submit">
            関連付ける
          </Button>
        </div>
      )}
    </div>
  );
}

/** §9.2 関連付け. The target comes from the chosen kind's own list (UX
 * audit C9), the same shape the Journey link above uses. */
function ArtifactLinkForm({ gapKey }: { gapKey: string }) {
  const add = useAddProductGapArtifactLink(gapKey);
  const [linkKind, setLinkKind] = useState<ProductGapArtifactLinkKind>("ux_requirement");
  const [targetRef, setTargetRef] = useState("");
  const [rejection, setRejection] = useState<Rejection | null>(null);
  useDirtyGuard(`gap-artifact-link:${gapKey}`, targetRef !== "");

  function submit() {
    setRejection(null);
    add.mutate(
      { link_kind: linkKind, target_ref: targetRef },
      {
        onSuccess: () => { setTargetRef(""); toast.success("関連付けを記録しました"); },
        onError: (error) => setRejection(rejectionFromError(error, "関連付けできませんでした")),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-artifact-link-form">
      <p className="text-xs font-semibold">関連付け</p>
      <div className="flex flex-wrap gap-2">
        <Select
          aria-label="関連付け先の種類"
          value={linkKind}
          onChange={(e) => { setLinkKind(e.target.value as ProductGapArtifactLinkKind); setTargetRef(""); }}
        >
          {ARTIFACT_LINK_KINDS.map((k) => <option key={k} value={k}>{GAP_ARTIFACT_LINK_KIND_LABEL[k]}</option>)}
        </Select>
        <ReferenceTargetPicker
          kind={linkKind}
          value={targetRef}
          onChange={setTargetRef}
          ariaLabel="関連付け先"
          testId="gap-artifact-link-target"
        />
        <Button size="sm" variant="outline" disabled={!targetRef.trim() || add.isPending} onClick={submit} data-testid="gap-artifact-link-submit">
          関連付ける
        </Button>
      </div>
      {rejection && <RejectedNotice {...rejection} testId="gap-artifact-link-rejected" />}
    </div>
  );
}

/** The full Gap detail pane: source federation, current/target state,
 * interpretation, evidence, artifact links, decision history, and the
 * manual action controls (§9.2). Fetches `GET /product-gaps/{key}`
 * separately from the Workbench list, since the six axes (§5.1) live on the
 * detail response, not the list entry. */
/**
 * §5.8/§5.8.1's two axes, rendered as two facts wherever a Gap reference
 * offers a link: whether a screen exists, and whether that URL opens ON the
 * subject. A `screen_only` link says so, so the developer knows they still
 * have to find the subject there -- one label promising both is the #366
 * one-word-two-facts defect.
 *
 * The route ALWAYS comes from the server's per-kind table. Never build one
 * here: "which screen owns this kind" must have one answer, and a second one
 * assembled client-side is the defect this Epic exists to prevent (§0-1).
 */
function ReferenceDeepLink({
  route, state, targetState, testIdBase, rowId, unavailableNote,
}: {
  route: string | null;
  state: ProductDeepLinkState;
  targetState: ProductDeepLinkTargetState;
  testIdBase: string;
  rowId: number;
  unavailableNote?: string;
}) {
  if (state !== "available" || !route) {
    return (
      <span className="text-muted-foreground" data-testid={`${testIdBase}-unavailable-${rowId}`}>
        {DEEP_LINK_STATE_LABEL.unavailable}{unavailableNote}
      </span>
    );
  }
  return (
    <>
      <Link
        to={route}
        className="text-primary underline"
        data-testid={`${testIdBase}-${rowId}`}
        data-target-state={targetState}
      >
        {targetState === "selected" ? "対象を開く" : "画面を開く"}
      </Link>
      {targetState === "screen_only" && (
        <span className="ml-2 text-muted-foreground" data-testid={`${testIdBase}-screen-only-${rowId}`}>
          {DEEP_LINK_TARGET_STATE_LABEL.screen_only}
        </span>
      )}
    </>
  );
}

export function GapDetailPanel({ gapKey, workbench }: { gapKey: string; workbench: GapWorkbenchOut }) {
  const detail = useProductGapDetail(gapKey);

  if (detail.isLoading) {
    return <p className="text-sm text-muted-foreground" data-testid="gap-detail-loading">Gap を読み込んでいます…</p>;
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm" role="alert" data-testid="gap-detail-error">
        <p className="font-medium text-destructive">Gap を取得できませんでした。</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => detail.refetch()}>再試行</Button>
      </div>
    );
  }
  const gap = detail.data;
  const revision = gap.current_revision;

  return (
    <div className="space-y-3" data-testid={`gap-detail-${gap.gap_key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{gap.title || gap.gap_key}</h3>
        <Badge variant="outline">{GAP_LIFECYCLE_LABEL[gap.lifecycle]}</Badge>
        {gap.priority_band !== "unset" && <Badge variant="secondary">{GAP_PRIORITY_BAND_LABEL[gap.priority_band]}</Badge>}
        {gap.recheck_state !== "current" && <Badge variant="warning">{RECHECK_STATE_LABEL[gap.recheck_state]}</Badge>}
      </div>

      {gap.read_flags.length > 0 && (
        <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-300" data-testid="gap-detail-read-flags">
          {gap.read_flags.map((f) => <li key={f}>・{GAP_READ_FLAG_LABEL[f]}</li>)}
        </ul>
      )}

      {revision && (
        <dl className="space-y-1.5 text-sm">
          <div>
            <dt className="text-xs font-medium text-muted-foreground">現在状態</dt>
            <dd data-testid="gap-detail-current-state">{revision.current_state || "(未記入)"}</dd>
          </div>
          <div>
            {/* §C: the EFFECTIVE target (`gap.effective_target_state` /
                `effective_target_availability`), not the revision's own
                (possibly-empty) `target_state` column -- an
                `inherited_from_milestone` Gap stores no target text of its
                own (§5.3), and `unavailable` must never render as an empty
                target or "no target set" (§0 invariant 8). */}
            <dt className="text-xs font-medium text-muted-foreground">
              目標状態({GAP_EFFECTIVE_TARGET_AVAILABILITY_LABEL[gap.effective_target_availability]})
            </dt>
            <dd data-testid="gap-detail-target-state" data-target-availability={gap.effective_target_availability}>
              {gap.effective_target_availability === "unavailable"
                ? "Milestone の目標状態を取得できませんでした。Milestone 側の内容を確認してください。"
                : gap.effective_target_availability === "unknown"
                  ? "まだ決めていません"
                  : gap.effective_target_state || "(未記入)"}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">解釈</dt>
            <dd data-testid="gap-detail-interpretation">{revision.interpretation || "(未記入)"}</dd>
          </div>
          {/* §5.11: the Gap's Journey connection has ONE writable home,
              `ux_journey_upstream_ref`, so the Gap side has no link rows of
              its own to show. The server reads it back by reverse lookup and
              reports it here; this list is never derived client-side. */}
          <div>
            <dt className="text-xs font-medium text-muted-foreground">この Gap を解消する Journey</dt>
            <dd data-testid="gap-detail-journey-links">
              {gap.journey_links.length === 0 ? (
                <span className="text-muted-foreground">まだ紐づいていません</span>
              ) : (
                <ul className="space-y-1">
                  {gap.journey_links.map((link) => (
                    <li key={link.journey_key} data-testid={`gap-journey-link-${link.journey_key}`}>
                      <Link
                        className="underline"
                        to={`/ux-design-studio?tab=journeys&journey=${encodeURIComponent(link.journey_key)}`}
                      >
                        {link.title || link.journey_key}
                      </Link>
                      <span className="ml-2 text-xs text-muted-foreground">
                        {link.perspective === "as_is" ? "現状" : "目標"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </dd>
          </div>
          <div className="text-xs text-muted-foreground">
            執筆: {AUTHORSHIP_LABEL[revision.authored_by_kind]}
          </div>
        </dl>
      )}

      <div>
        <h4 className="text-xs font-semibold">検出元</h4>
        {gap.source_refs.length === 0 ? (
          <p className="text-xs text-muted-foreground">検出元は登録されていません。</p>
        ) : (
          <ul className="mt-1 space-y-1.5">
            {gap.source_refs.map((s) => {
              const shared = sharedGapKeysForSource(workbench, s.source_kind, s.source_ref, gap.gap_key);
              const deepLink = workbench.entries
                .find((e) => e.gap_key === gap.gap_key)
                ?.deep_links.find((d) => d.source_kind === s.source_kind && d.source_ref === s.source_ref);
              return (
                <li key={s.id} className="rounded border p-2 text-xs" data-testid={`gap-source-${s.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{GAP_SOURCE_KIND_LABEL[s.source_kind]}</Badge>
                    <span>{GAP_SOURCE_STATE_LABEL[s.source_state]}</span>
                    {s.severity && (
                      <span className="text-muted-foreground">
                        ({s.severity_vocabulary ?? "?"}: {s.severity})
                      </span>
                    )}
                  </div>
                  {s.detail && <p className="mt-1 text-muted-foreground">{s.detail}</p>}
                  <div className="mt-1">
                    <ReferenceDeepLink
                      route={deepLink?.route ?? null}
                      state={deepLink?.deep_link_state ?? "unavailable"}
                      targetState={deepLink?.deep_link_target_state ?? "unavailable"}
                      testIdBase="gap-source-deep-link"
                      rowId={s.id}
                      unavailableNote="(この検出元にはまだ専用の画面がありません)"
                    />
                  </div>
                  {shared.length > 0 && (
                    <p className="mt-1 text-muted-foreground">
                      同じ検出元を参照している他の Gap: {shared.join(", ")}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {gap.evidence_refs.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold">証跡</h4>
          {/* §5.8: the route comes from the server's per-kind table. Never
              build one here -- "which screen owns this kind" must have one
              answer, and a second one assembled client-side is the defect
              this Epic exists to prevent (§0-1). A kind with no screen
              (`human_report` / `external_report` / `other`) says so. */}
          <ul className="mt-1 space-y-1 text-xs" data-testid="gap-evidence-list">
            {gap.evidence_refs.map((ev) => (
              <li key={ev.id} data-testid={`gap-evidence-${ev.id}`}>
                <span>{GAP_EVIDENCE_KIND_LABEL[ev.evidence_kind]}: {ev.evidence_ref}</span>
                <span className="ml-2">
                  <ReferenceDeepLink
                    route={ev.deep_link}
                    state={ev.deep_link_state}
                    targetState={ev.deep_link_target_state}
                    testIdBase="gap-evidence-deep-link"
                    rowId={ev.id}
                  />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {gap.artifact_links.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold">関連付け済み</h4>
          {/* §5.8, same server-owned table as evidence above.
              `product_feature` has an API but no screen, so it renders as
              unavailable rather than as a fabricated URL. */}
          <ul className="mt-1 space-y-1 text-xs" data-testid="gap-artifact-link-list">
            {gap.artifact_links.map((link) => (
              <li key={link.id} data-testid={`gap-artifact-link-${link.id}`}>
                <span>{GAP_ARTIFACT_LINK_KIND_LABEL[link.link_kind]}: {link.target_ref}</span>
                <span className="ml-2">
                  <ReferenceDeepLink
                    route={link.deep_link}
                    state={link.deep_link_state}
                    targetState={link.deep_link_target_state}
                    testIdBase="gap-artifact-link-deep-link"
                    rowId={link.id}
                  />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <GapRevisionForm gapKey={gap.gap_key} current={gap.current_revision} />

      <GapSourceRefForm gapKey={gap.gap_key} />

      <GapEvidenceRefForm gapKey={gap.gap_key} />

      <div className="space-y-2 rounded border p-2">
        <p className="text-xs font-semibold">解消状態を変える(人間の判断として記録されます)</p>
        <DecisionRationaleControls gap={gap} onStale={() => detail.refetch()} />
        <PrioritizeControls gap={gap} onStale={() => detail.refetch()} />
      </div>

      <GapResolutionChoiceForm gap={gap} />

      <JourneyLinkForm gapKey={gap.gap_key} />

      <ArtifactLinkForm gapKey={gap.gap_key} />

      {gap.decisions.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold">決定の履歴</h4>
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground" data-testid="gap-decision-history">
            {gap.decisions.map((d) => (
              <li key={d.id}>
                {GAP_DECISION_LABEL[d.decision]}
                {d.decision === "prioritize" && `(${GAP_PRIORITY_BAND_LABEL[d.priority_band]})`}
                {" "}— {d.decided_by ?? "(記録なし)"} / {formatTimestamp(d.created_at)}
                {d.rationale ? `:${d.rationale}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
