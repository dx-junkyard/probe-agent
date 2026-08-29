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
import {
  useAddProductGapArtifactLink, useAddProductGapRevision, useCreateProductGap,
  useLinkProductGapToJourney, useProductGapDetail, useRecordProductGapDecision,
  useUxJourneys,
} from "@/api/hooks";
import type {
  GapWorkbenchEntryOut, GapWorkbenchOut, ObjectiveMapOut,
  ProductGapArtifactLinkKind, ProductGapLifecycle, ProductGapPriorityBand,
  ProductGapTargetMode,
} from "@/api/types";
import { formatTimestamp } from "@/lib/utils";
import {
  AUTHORSHIP_LABEL, DEEP_LINK_STATE_LABEL, GAP_ARTIFACT_LINK_KIND_LABEL,
  GAP_DECISION_LABEL, GAP_EFFECTIVE_TARGET_AVAILABILITY_LABEL, GAP_LIFECYCLE_LABEL,
  GAP_PRIORITY_BAND_LABEL, GAP_READ_FLAG_LABEL, GAP_SOURCE_KIND_LABEL,
  GAP_SOURCE_STATE_LABEL, RECHECK_STATE_LABEL, sharedGapKeysForSource,
  isStaleDigestErrorCode,
  type GapWorkbenchFilters,
} from "./model";
import { StaleDigestNotice } from "./objective-forms";

const LIFECYCLE_VALUES: ProductGapLifecycle[] = [
  "open", "acknowledged", "deferred", "resolved", "rejected", "obsolete",
];
const PRIORITY_BAND_VALUES: ProductGapPriorityBand[] = ["unset", "watch", "next", "now"];
const ARTIFACT_LINK_KINDS: ProductGapArtifactLinkKind[] = [
  "issue_draft", "ux_requirement", "product_feature", "solution_design",
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

/** Every decision/assessment on this screen sends `decisionDigest` -- read
 * from the Gap's OWN `decision_digest` field (§B/§10.1), NEVER
 * `current_revision.content_digest`: an `inherited_from_milestone` Gap is
 * judged partly against the Milestone's target, so the two differ and the
 * revision digest would 409 on every decision. A stale-digest 409 renders
 * `StaleDigestNotice` -- reload and re-read, never a silent/automatic
 * retry. */
function DecisionRationaleControls({
  gapKey, decisionDigest, onStale,
}: {
  gapKey: string;
  decisionDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductGapDecision(gapKey);
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<{ code: string; message: string } | null>(null);
  const [stale, setStale] = useState(false);

  function submit(decision: "acknowledge" | "defer" | "resolve" | "reopen") {
    setRejection(null);
    setStale(false);
    record.mutate(
      { decision, rationale, captured_digest: decisionDigest },
      {
        onSuccess: () => {
          setRationale("");
          toast.success(`「${GAP_DECISION_LABEL[decision]}」を記録しました`);
        },
        onError: (error) => {
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          setRejection({ code: apiError.code ?? "unknown", message: apiError.detail || "記録できませんでした" });
        },
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="gap-decision-controls">
      <Textarea placeholder="理由(任意)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <div className="flex flex-wrap gap-2">
        {(["acknowledge", "defer", "resolve", "reopen"] as const).map((d) => (
          <Button key={d} size="sm" variant="outline" disabled={record.isPending} onClick={() => submit(d)} data-testid={`gap-decision-${d}`}>
            {GAP_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
      {rejection && (
        <div className="rounded border border-destructive p-2 text-xs" data-testid="gap-decision-rejected">
          <span className="font-mono text-destructive">{rejection.code}</span>
          <span className="ml-2">{rejection.message}</span>
        </div>
      )}
    </div>
  );
}

function PrioritizeControls({
  gapKey, currentBand, decisionDigest, onStale,
}: {
  gapKey: string;
  currentBand: ProductGapPriorityBand;
  decisionDigest: string;
  onStale: () => void;
}) {
  const record = useRecordProductGapDecision(gapKey);
  const [band, setBand] = useState<ProductGapPriorityBand>(currentBand);
  const [stale, setStale] = useState(false);

  function submit() {
    setStale(false);
    record.mutate(
      { decision: "prioritize", priority_band: band, captured_digest: decisionDigest },
      {
        onSuccess: () => toast.success("優先バンドを記録しました"),
        onError: (error) => {
          const apiError = error as ApiError;
          if (isStaleDigestErrorCode(apiError.code)) { setStale(true); return; }
          toast.error(apiError.detail || "記録できませんでした");
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
        <Button size="sm" variant="outline" disabled={record.isPending} onClick={submit} data-testid="gap-decision-prioritize">
          {GAP_DECISION_LABEL.prioritize}
        </Button>
      </div>
      {stale && <StaleDigestNotice onRetry={() => { setStale(false); onStale(); }} />}
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

  function submit() {
    setRejection(null);
    create.mutate(
      { milestone_key: milestoneKey, gap_key: gapKey.trim() },
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
        <Select aria-label="所属する Milestone" value={milestoneKey} onChange={(e) => setMilestoneKey(e.target.value)}>
          {milestoneKeys.length === 0 && <option value="">Milestone がありません</option>}
          {milestoneKeys.map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <Input aria-label="gap_key" placeholder="gap_key" value={gapKey} onChange={(e) => setGapKey(e.target.value)} className="max-w-xs" />
        <Button size="sm" variant="outline" disabled={!milestoneKey || !gapKey.trim() || create.isPending} onClick={submit} data-testid="create-gap-submit">
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

function GapRevisionForm({ gapKey }: { gapKey: string }) {
  const add = useAddProductGapRevision(gapKey);
  const [title, setTitle] = useState("");
  const [currentState, setCurrentState] = useState("");
  const [targetState, setTargetState] = useState("");
  const [targetStateMode, setTargetStateMode] = useState<ProductGapTargetMode>("unknown");
  const [interpretation, setInterpretation] = useState("");

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

function ArtifactLinkForm({ gapKey }: { gapKey: string }) {
  const add = useAddProductGapArtifactLink(gapKey);
  const [linkKind, setLinkKind] = useState<ProductGapArtifactLinkKind>("issue_draft");
  const [targetRef, setTargetRef] = useState("");

  function submit() {
    add.mutate(
      { link_kind: linkKind, target_ref: targetRef },
      {
        onSuccess: () => { setTargetRef(""); toast.success("関連付けを記録しました"); },
        onError: (error) => toast.error((error as ApiError).detail || "関連付けできませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="gap-artifact-link-form">
      <p className="text-xs font-semibold">関連付け</p>
      <div className="flex flex-wrap gap-2">
        <Select aria-label="関連付け先の種類" value={linkKind} onChange={(e) => setLinkKind(e.target.value as ProductGapArtifactLinkKind)}>
          {ARTIFACT_LINK_KINDS.map((k) => <option key={k} value={k}>{GAP_ARTIFACT_LINK_KIND_LABEL[k]}</option>)}
        </Select>
        <Input
          placeholder="参照(key)"
          value={targetRef}
          onChange={(e) => setTargetRef(e.target.value)}
          className="max-w-xs"
        />
        <Button size="sm" variant="outline" disabled={!targetRef.trim() || add.isPending} onClick={submit}>
          関連付ける
        </Button>
      </div>
    </div>
  );
}

/** The full Gap detail pane: source federation, current/target state,
 * interpretation, evidence, artifact links, decision history, and the
 * manual action controls (§9.2). Fetches `GET /product-gaps/{key}`
 * separately from the Workbench list, since the six axes (§5.1) live on the
 * detail response, not the list entry. */
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
                    {deepLink?.deep_link_state === "available" && deepLink.route ? (
                      <Link to={deepLink.route} className="text-primary underline" data-testid={`gap-source-deep-link-${s.id}`}>
                        検出元の画面を開く
                      </Link>
                    ) : (
                      <span className="text-muted-foreground" data-testid={`gap-source-deep-link-unavailable-${s.id}`}>
                        {DEEP_LINK_STATE_LABEL.unavailable}(この検出元にはまだ専用の画面がありません)
                      </span>
                    )}
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
          <ul className="mt-1 space-y-1 text-xs">
            {gap.evidence_refs.map((ev) => (
              <li key={ev.id}>{ev.evidence_kind}: {ev.evidence_ref}</li>
            ))}
          </ul>
        </div>
      )}

      {gap.artifact_links.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold">関連付け済み</h4>
          <ul className="mt-1 space-y-1 text-xs">
            {gap.artifact_links.map((link) => (
              <li key={link.id}>{GAP_ARTIFACT_LINK_KIND_LABEL[link.link_kind]}: {link.target_ref}</li>
            ))}
          </ul>
        </div>
      )}

      <GapRevisionForm gapKey={gap.gap_key} />

      <div className="space-y-2 rounded border p-2">
        <p className="text-xs font-semibold">解消状態を変える(人間の判断として記録されます)</p>
        <DecisionRationaleControls
          gapKey={gap.gap_key} decisionDigest={gap.decision_digest} onStale={() => detail.refetch()}
        />
        <PrioritizeControls
          gapKey={gap.gap_key} currentBand={gap.priority_band} decisionDigest={gap.decision_digest}
          onStale={() => detail.refetch()}
        />
      </div>

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
