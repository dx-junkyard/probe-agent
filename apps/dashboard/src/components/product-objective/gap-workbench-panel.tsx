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
  useAddProductGapArtifactLink, useProductGapDetail, useRecordProductGapDecision,
} from "@/api/hooks";
import type {
  GapWorkbenchEntryOut, GapWorkbenchOut, ObjectiveMapOut,
  ProductGapArtifactLinkKind, ProductGapLifecycle, ProductGapPriorityBand,
} from "@/api/types";
import { formatTimestamp } from "@/lib/utils";
import {
  AUTHORSHIP_LABEL, DEEP_LINK_STATE_LABEL, GAP_ARTIFACT_LINK_KIND_LABEL,
  GAP_DECISION_LABEL, GAP_LIFECYCLE_LABEL, GAP_PRIORITY_BAND_LABEL,
  GAP_READ_FLAG_LABEL, GAP_SOURCE_KIND_LABEL, GAP_SOURCE_STATE_LABEL,
  RECHECK_STATE_LABEL, sharedGapKeysForSource,
  type GapWorkbenchFilters,
} from "./model";

const LIFECYCLE_VALUES: ProductGapLifecycle[] = [
  "open", "acknowledged", "deferred", "resolved", "rejected", "obsolete",
];
const PRIORITY_BAND_VALUES: ProductGapPriorityBand[] = ["unset", "watch", "next", "now"];
const ARTIFACT_LINK_KINDS: ProductGapArtifactLinkKind[] = [
  "issue_draft", "ux_journey", "ux_requirement", "product_feature", "solution_design",
];

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

function DecisionRationaleControls({ gapKey }: { gapKey: string }) {
  const record = useRecordProductGapDecision(gapKey);
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<{ code: string; message: string } | null>(null);

  function submit(decision: "acknowledge" | "defer" | "resolve" | "reopen") {
    setRejection(null);
    record.mutate(
      { decision, rationale },
      {
        onSuccess: () => {
          setRationale("");
          toast.success(`「${GAP_DECISION_LABEL[decision]}」を記録しました`);
        },
        onError: (error) => {
          const apiError = error as ApiError;
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
      {rejection && (
        <div className="rounded border border-destructive p-2 text-xs" data-testid="gap-decision-rejected">
          <span className="font-mono text-destructive">{rejection.code}</span>
          <span className="ml-2">{rejection.message}</span>
        </div>
      )}
    </div>
  );
}

function PrioritizeControls({ gapKey, currentBand }: { gapKey: string; currentBand: ProductGapPriorityBand }) {
  const record = useRecordProductGapDecision(gapKey);
  const [band, setBand] = useState<ProductGapPriorityBand>(currentBand);

  function submit() {
    record.mutate(
      { decision: "prioritize", priority_band: band },
      {
        onSuccess: () => toast.success("優先バンドを記録しました"),
        onError: (error) => toast.error((error as ApiError).detail || "記録できませんでした"),
      },
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="gap-prioritize-controls">
      <Select aria-label="優先バンド" value={band} onChange={(e) => setBand(e.target.value as ProductGapPriorityBand)}>
        {PRIORITY_BAND_VALUES.map((v) => <option key={v} value={v}>{GAP_PRIORITY_BAND_LABEL[v]}</option>)}
      </Select>
      <Button size="sm" variant="outline" disabled={record.isPending} onClick={submit} data-testid="gap-decision-prioritize">
        {GAP_DECISION_LABEL.prioritize}
      </Button>
    </div>
  );
}

function ArtifactLinkForm({ gapKey }: { gapKey: string }) {
  const add = useAddProductGapArtifactLink(gapKey);
  const [linkKind, setLinkKind] = useState<ProductGapArtifactLinkKind>("ux_journey");
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
            <dt className="text-xs font-medium text-muted-foreground">
              目標状態
              {revision.target_state_mode === "inherited_from_milestone" && "(Milestone から継承)"}
              {revision.target_state_mode === "unknown" && "(未定)"}
            </dt>
            <dd data-testid="gap-detail-target-state">
              {revision.target_state_mode === "unknown" ? "まだ決めていません" : revision.target_state || "(未記入)"}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">解釈</dt>
            <dd data-testid="gap-detail-interpretation">{revision.interpretation || "(未記入)"}</dd>
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

      <div className="space-y-2 rounded border p-2">
        <p className="text-xs font-semibold">解消状態を変える(人間の判断として記録されます)</p>
        <DecisionRationaleControls gapKey={gap.gap_key} />
        <PrioritizeControls gapKey={gap.gap_key} currentBand={gap.priority_band} />
      </div>

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
