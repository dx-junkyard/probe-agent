// UX audit 2026-11-17 C2 / C9: pick a reference target from the list of
// that kind instead of typing its key.
//
// One component per kind, chosen by a `switch`, so only the selected kind's
// query runs (hooks cannot be called conditionally inside one component, and
// running every list query for every picker would read the whole System on
// each form mount). Switching kind remounts the child, and the parent resets
// the value it holds -- a key picked for kind A must never be sent as kind B.
//
// Every option VALUE is the stable reference the server's own resolver for
// that kind reads (the per-kind table in `product-objective-lineage.md` §4.6 /
// §5.4 and `ux-design-lineage.md` §3.3). Where that reference is a documented
// composite (`code|subject_kind|subject_ref`), it is assembled from the
// fields of the SAME canonical projection the resolver calls -- never from a
// display name, never by similarity. Whether the server can actually resolve
// it is still the server's answer: a refusal comes back as its own code.
//
// A kind with no list endpoint keeps a text field and SAYS so
// (「一覧から選べない種類です」). It is never silently offered as if it were a
// list, and a list that failed to load is never shown as an empty one.

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  useComponents, useEvolutionNodes, useExperiments, useFlowSubjects, useFunctionalLineage,
  useGapWorkbench, useInterviewCapabilityGraph, useInterviewSessions, useIssueDrafts,
  useObjectiveMap, useProductFeatures, useSnapshots, useSolutionDesigns, useStakeholderNeeds,
  useSystemPurposeChain, useSystemUnderstanding, useUnderstandingBrief, useUxRequirements,
  useValueNetwork,
} from "@/api/hooks";

/** Every reference kind a form on these screens can ask for. A union of the
 * server's per-endpoint vocabularies (`ProductRefKind`, `UxRefKind`,
 * `SolutionTargetKind`, `ProductGapSourceKind`, `ProductGapEvidenceKind`,
 * `ProductGapArtifactLinkKind`); a form passes only its own endpoint's
 * kinds. */
export type ReferenceKind =
  | "vision_claim" | "purpose_element" | "purpose_relation" | "capability_entity" | "capability"
  | "stakeholder_need"
  | "product_objective" | "product_milestone" | "product_gap" | "product_feature"
  | "ux_requirement" | "solution_design" | "issue_draft"
  | "evolution_node" | "component" | "runtime_flow" | "static_flow"
  | "cell_definition" | "cell_binding" | "probe_point"
  | "system_understanding_gap" | "functional_lineage_gap" | "value_network_notice"
  | "experiment"
  | (string & {});

export interface ReferenceOption {
  value: string;
  label: string;
}

type CandidateState =
  | { status: "loading" }
  | { status: "error"; retry: () => void }
  | { status: "ready"; options: ReferenceOption[]; emptyNote: string };

export interface ReferenceTargetPickerProps {
  kind: ReferenceKind;
  value: string;
  onChange: (value: string) => void;
  /** `static_flow` only: the Snapshot the entry point belongs to. */
  snapshotId?: string;
  onSnapshotIdChange?: (snapshotId: string) => void;
  /** Shown when the kind has no list. Defaults to a generic hint. */
  manualPlaceholder?: string;
  ariaLabel: string;
  testId: string;
}

/** The newest Interview session's id: the session every "latest" resolver on
 * the server reads (`ORDER BY id DESC LIMIT 1`). `undefined` while loading,
 * `null` when the System has none. */
function useNewestSessionId(): { id: number | null | undefined; isError: boolean; retry: () => void } {
  const sessions = useInterviewSessions();
  if (sessions.isError) return { id: undefined, isError: true, retry: () => sessions.refetch() };
  if (!sessions.data) return { id: undefined, isError: false, retry: () => sessions.refetch() };
  const ids = sessions.data.map((s) => s.id);
  return { id: ids.length === 0 ? null : Math.max(...ids), isError: false, retry: () => sessions.refetch() };
}

function fromQuery<T>(
  query: { isLoading: boolean; isError: boolean; data: T | undefined; refetch: () => unknown },
  toOptions: (data: T) => ReferenceOption[],
  emptyNote: string,
): CandidateState {
  if (query.isError && !query.data) return { status: "error", retry: () => { void query.refetch(); } };
  if (!query.data) return { status: "loading" };
  return { status: "ready", options: toOptions(query.data), emptyNote };
}

function CandidateSelect({
  state, value, onChange, ariaLabel, testId,
}: {
  state: CandidateState;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  testId: string;
}) {
  if (state.status === "loading") {
    return <p className="text-xs text-muted-foreground" data-testid={`${testId}-loading`}>候補を読み込んでいます…</p>;
  }
  if (state.status === "error") {
    // 「取得できなかった」 is not 「候補が 0 件」 (§0 invariant 8).
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs" role="alert" data-testid={`${testId}-error`}>
        <span className="text-destructive">候補の一覧を取得できませんでした。</span>
        <Button size="sm" variant="outline" onClick={state.retry}>再試行</Button>
      </div>
    );
  }
  if (state.options.length === 0) {
    return <p className="text-xs text-muted-foreground" data-testid={`${testId}-empty`}>{state.emptyNote}</p>;
  }
  return (
    <Select aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testId}>
      <option value="">選択してください</option>
      {state.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </Select>
  );
}

type KindPickerProps = Omit<ReferenceTargetPickerProps, "kind">;

function VisionClaimPicker(props: KindPickerProps) {
  const newest = useNewestSessionId();
  const brief = useUnderstandingBrief(newest.id ?? null);
  let state: CandidateState;
  if (newest.isError) state = { status: "error", retry: newest.retry };
  else if (newest.id === undefined) state = { status: "loading" };
  else if (newest.id === null) state = { status: "ready", options: [], emptyNote: "Interview がまだ無いため、参照できる Vision がありません。" };
  else {
    state = fromQuery(brief, (b) => (b.vision ? [{ value: b.vision.name, label: `${b.vision.name}(${b.vision.confirmation_label})` }] : []),
      "現在の理解に Vision がありません。Interview で Vision を確認してください。");
  }
  return <CandidateSelect state={state} {...pick(props)} />;
}

function PurposeElementPicker(props: KindPickerProps) {
  const chain = useSystemPurposeChain();
  const state = fromQuery(chain, (c) => c.elements.map((e) => ({
    value: e.id, label: `${e.display_statement || e.statement || e.id}(${e.confirmation_label})`,
  })), "参照できる Purpose 要素がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function PurposeRelationPicker(props: KindPickerProps) {
  const chain = useSystemPurposeChain();
  const state = fromQuery(chain, (c) => {
    const name = (id: string) => {
      const e = c.elements.find((el) => el.id === id);
      return e ? (e.display_statement || e.statement || id) : id;
    };
    return c.relations.map((r) => ({ value: r.id, label: `${name(r.source_id)} → ${name(r.target_id)}(${r.status_label})` }));
  }, "参照できる Purpose の関係がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function CapabilityEntityPicker(props: KindPickerProps) {
  const newest = useNewestSessionId();
  const graph = useInterviewCapabilityGraph(newest.id ?? null);
  let state: CandidateState;
  if (newest.isError) state = { status: "error", retry: newest.retry };
  else if (newest.id === undefined) state = { status: "loading" };
  else if (newest.id === null || (graph.isError && (graph.error as { status?: number } | null)?.status === 404)) {
    // 404 here is the server's own "no confirmed capability graph yet" --
    // a real answer, not a failed read.
    state = { status: "ready", options: [], emptyNote: "確認済みの Capability がまだありません。Interview で Capability を確認してください。" };
  } else {
    state = fromQuery(graph, (g) => g.nodes.map((n) => ({ value: String(n.entity_id), label: `${n.name}(${n.entity_kind})` })),
      "確認済みの Capability がまだありません。");
  }
  return <CandidateSelect state={state} {...pick(props)} />;
}

function StakeholderNeedPicker(props: KindPickerProps) {
  const needs = useStakeholderNeeds();
  const state = fromQuery(needs, (d) => d.needs.map((n) => ({
    value: n.need_key, label: n.statement ? `${n.need_key}: ${n.statement}` : n.need_key,
  })), "この System にはまだ Need がありません。Stakeholder Value Network で作成してください。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function ObjectivePicker(props: KindPickerProps & { excludeKey?: string }) {
  const map = useObjectiveMap();
  const state = fromQuery(map, (m) => m.nodes
    .filter((n) => n.objective_key !== props.excludeKey)
    .map((n) => ({ value: n.objective_key, label: n.title ? `${n.title}(${n.objective_key})` : n.objective_key })),
  "選べる Objective がありません。Objective Map で作成してください。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function MilestonePicker(props: KindPickerProps) {
  const map = useObjectiveMap();
  const state = fromQuery(map, (m) => m.nodes.flatMap((n) => n.milestones.map((ms) => ({
    value: ms.milestone_key,
    label: `${ms.title || ms.milestone_key}(Objective: ${n.title || n.objective_key})`,
  }))), "この System にはまだ Milestone がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function GapPicker(props: KindPickerProps) {
  const workbench = useGapWorkbench();
  const state = fromQuery(workbench, (w) => w.entries.map((e) => ({
    value: e.gap_key, label: e.title ? `${e.title}(${e.gap_key})` : e.gap_key,
  })), "この System にはまだ Gap がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function FeaturePicker(props: KindPickerProps) {
  const features = useProductFeatures();
  const state = fromQuery(features, (d) => d.features.map((f) => ({
    value: f.feature_key, label: f.title ? `${f.title}(${f.feature_key})` : f.feature_key,
  })), "この System にはまだ Feature がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function RequirementPicker(props: KindPickerProps) {
  const reqs = useUxRequirements();
  const state = fromQuery(reqs, (d) => d.requirements.map((r) => ({
    value: r.requirement_key, label: r.statement ? `${r.requirement_key}: ${r.statement}` : r.requirement_key,
  })), "この System にはまだ Requirement がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function SolutionDesignPicker(props: KindPickerProps) {
  const designs = useSolutionDesigns();
  const state = fromQuery(designs, (d) => d.designs.map((s) => ({
    value: s.design_key, label: s.title ? `${s.title}(${s.design_key})` : s.design_key,
  })), "この System にはまだ Solution Design がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function IssueDraftPicker(props: KindPickerProps) {
  const drafts = useIssueDrafts();
  const state = fromQuery(drafts, (d) => d.map((i) => ({ value: String(i.id), label: `#${i.id} ${i.title}` })),
    "この System にはまだ Issue Draft がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function EvolutionNodePicker(props: KindPickerProps) {
  const nodes = useEvolutionNodes();
  const state = fromQuery(nodes, (d) => d.nodes.map((n) => ({
    value: n.node_key, label: n.display_name ? `${n.display_name}(${n.node_key})` : n.node_key,
  })), "この System にはまだ Evolution Node がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function ComponentPicker(props: KindPickerProps) {
  const components = useComponents();
  const state = fromQuery(components, (d) => d.map((c) => ({ value: c.component_id, label: c.component_id })),
    "この System にはまだ Component がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function RuntimeFlowPicker(props: KindPickerProps) {
  const subjects = useFlowSubjects(null);
  const state = fromQuery(subjects, (d) => d.runtime_flows.map((f) => ({
    value: f.subject_ref, label: f.label && f.label !== f.subject_ref ? `${f.label}(${f.subject_ref})` : f.subject_ref,
  })), "実行時 Flow はまだ観測も登録もされていません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

/** `static_flow` needs TWO choices: the Snapshot (the link's required
 * `captured_snapshot_id`) and the entry point inside it. The entry points
 * are listed for the chosen Snapshot only -- an entry point id is only
 * meaningful within one Snapshot. */
function StaticFlowPicker(props: KindPickerProps) {
  const snapshots = useSnapshots();
  const snapshotId = props.snapshotId ?? "";
  const subjects = useFlowSubjects(snapshotId ? Number(snapshotId) : null);
  const snapshotState = fromQuery(snapshots, (d) => d.map((s) => ({
    value: String(s.id), label: `#${s.id} ${s.commit_sha.slice(0, 12)}(${s.status})`,
  })), "Snapshot がまだありません。Repository 画面で作成してください。");
  const flowState: CandidateState = !snapshotId
    ? { status: "ready", options: [], emptyNote: "先に Snapshot を選んでください。" }
    : fromQuery(subjects, (d) => d.static_flows.map((f) => ({
        value: f.subject_ref, label: f.label ? `${f.label}(${f.subject_ref})` : f.subject_ref,
      })), "この Snapshot には入口がありません。");
  return (
    <div className="flex flex-wrap gap-2">
      <CandidateSelect
        state={snapshotState}
        value={snapshotId}
        onChange={(v) => { props.onSnapshotIdChange?.(v); props.onChange(""); }}
        ariaLabel="Snapshot"
        testId={`${props.testId}-snapshot`}
      />
      <CandidateSelect state={flowState} {...pick(props)} />
    </div>
  );
}

function SystemUnderstandingGapPicker(props: KindPickerProps) {
  const su = useSystemUnderstanding();
  const state = fromQuery(su, (d) => d.gaps
    // `gap_key` is the triage identity the resolver reads; a gap without one
    // (an older server) has no reference to offer.
    .filter((g) => !!g.gap_key)
    .map((g) => ({ value: g.gap_key!, label: g.title || g.node_name || g.gap_key! })),
  "System Understanding に Gap がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function FunctionalLineageGapPicker(props: KindPickerProps) {
  const lineage = useFunctionalLineage();
  const state = fromQuery(lineage, (d) => d.gaps.map((g) => ({
    value: `${g.code}|${g.subject_kind}|${g.subject_ref}`,
    label: `${g.code}: ${g.subject_kind} ${g.subject_ref}`,
  })), "Functional Lineage に Gap がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function ValueNetworkNoticePicker(props: KindPickerProps) {
  const network = useValueNetwork();
  const state = fromQuery(network, (d) => d.notices.map((n) => ({
    value: `${n.code}|${n.subject_kind}|${n.subject_key}`,
    label: `${n.code}: ${n.subject_kind} ${n.subject_key}`,
  })), "Stakeholder Value Network に notice がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

function ExperimentPicker(props: KindPickerProps) {
  const experiments = useExperiments();
  const state = fromQuery(experiments, (d) => d.map((e) => ({
    value: String(e.id), label: `#${e.id} ${e.objective || e.feature_id}`,
  })), "この System にはまだ Experiment がありません。");
  return <CandidateSelect state={state} {...pick(props)} />;
}

/** A kind with no list endpoint: the one place a key is still typed, and the
 * screen says why. */
function ManualReferenceInput(props: KindPickerProps) {
  return (
    <div className="space-y-1" data-testid={`${props.testId}-manual`}>
      <p className="text-xs text-muted-foreground" data-testid={`${props.testId}-not-listable`}>
        一覧から選べない種類です。参照(key や id)を入力してください。形式が違う場合は保存時にサーバーが理由を返します。
      </p>
      <Input
        aria-label={props.ariaLabel}
        placeholder={props.manualPlaceholder ?? "参照(key / id)"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        data-testid={props.testId}
      />
    </div>
  );
}

function pick(props: KindPickerProps) {
  return { value: props.value, onChange: props.onChange, ariaLabel: props.ariaLabel, testId: props.testId };
}

export function ReferenceTargetPicker({ kind, ...rest }: ReferenceTargetPickerProps & { excludeKey?: string }) {
  switch (kind) {
    case "vision_claim": return <VisionClaimPicker {...rest} />;
    case "purpose_element": return <PurposeElementPicker {...rest} />;
    case "purpose_relation": return <PurposeRelationPicker {...rest} />;
    case "capability_entity":
    case "capability": return <CapabilityEntityPicker {...rest} />;
    case "stakeholder_need": return <StakeholderNeedPicker {...rest} />;
    case "product_objective": return <ObjectivePicker {...rest} />;
    case "product_milestone": return <MilestonePicker {...rest} />;
    case "product_gap": return <GapPicker {...rest} />;
    case "product_feature": return <FeaturePicker {...rest} />;
    case "ux_requirement": return <RequirementPicker {...rest} />;
    case "solution_design": return <SolutionDesignPicker {...rest} />;
    case "issue_draft": return <IssueDraftPicker {...rest} />;
    case "evolution_node": return <EvolutionNodePicker {...rest} />;
    case "component": return <ComponentPicker {...rest} />;
    case "runtime_flow": return <RuntimeFlowPicker {...rest} />;
    case "static_flow": return <StaticFlowPicker {...rest} />;
    case "system_understanding_gap": return <SystemUnderstandingGapPicker {...rest} />;
    case "functional_lineage_gap": return <FunctionalLineageGapPicker {...rest} />;
    case "value_network_notice": return <ValueNetworkNoticePicker {...rest} />;
    case "experiment": return <ExperimentPicker {...rest} />;
    default: return <ManualReferenceInput {...rest} />;
  }
}

/** Objective parent selection: same-System Objectives except itself. The
 * self-exclusion is only to keep an obviously meaningless choice off the
 * list -- cycles further up are the server's to refuse
 * (`product_objective_parent_cycle`), never checked here. */
export function ObjectiveParentPicker(props: KindPickerProps & { excludeKey: string }) {
  return <ObjectivePicker {...props} />;
}
