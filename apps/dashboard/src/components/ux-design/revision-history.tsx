// Issue #409: the append-only revision history and the revision-to-revision
// diff, for Journeys and for Requirements.
//
// This is a DIFFERENT question from the baseline diff. `baseline-diff`
// compares a to-be Journey against the as-is Journey it is meant to replace;
// this compares one revision of a single subject against another revision of
// that same subject -- "what did we change our minds about", not "what are we
// proposing to change about the system".
//
// Nothing here classifies anything. `revision_state`, `diff_state` and each
// entry's `change_kind` arrive already decided, and the server matches entries
// on exact `step_key` / `criterion_key` equality (contract §2.10) -- there is
// no client-side pairing to get wrong.

import { useState } from "react";
import { Select } from "@/components/ui/select";
import { formatTimestamp } from "@/lib/utils";
import {
  useUxJourneyRevisions,
  useUxJourneyRevisionDiff,
  useUxRequirementRevisions,
  useUxRequirementRevisionDiff,
} from "@/api/hooks";
import type { UxDiffChangeKind, UxDiffState } from "@/api/types";
import {
  AUTHORSHIP_LABEL,
  DIFF_CHANGE_KIND_LABEL,
  DIFF_STATE_LABEL,
  REVISION_STATE_LABEL,
} from "./model";
import { EmptyNote, LoadErrorCard, LoadingBlock, SectionHeading, StateBadge } from "./shared";

function changeTone(kind: UxDiffChangeKind): "warning" | "outline" | "secondary" {
  if (kind === "changed") return "warning";
  if (kind === "unchanged") return "outline";
  return "secondary";
}

type RevisionSummary = {
  revision_number: number;
  revision_state: string;
  authored_by_kind: string;
  change_note: string;
  created_by: string | null;
  created_at: number;
};

type DiffEntry = { key: string; change_kind: UxDiffChangeKind };

function DiffEntries({ state, entries }: { state: UxDiffState; entries: DiffEntry[] }) {
  if (state === "not_applicable") {
    return (
      <EmptyNote testId="ux-revision-diff-not-applicable">
        比較できる版がありません。この主題にはまだ 2 つ目の版がありません。
      </EmptyNote>
    );
  }
  if (state === "unavailable") {
    return <EmptyNote testId="ux-revision-diff-unavailable">{DIFF_STATE_LABEL.unavailable}</EmptyNote>;
  }
  if (entries.length === 0) {
    return <EmptyNote testId="ux-revision-diff-empty">比較対象の項目がありません。</EmptyNote>;
  }
  return (
    <ul className="space-y-1 text-xs" data-testid="ux-revision-diff-entries">
      {entries.map((entry) => (
        <li key={entry.key} className="rounded border p-2">
          <StateBadge label={DIFF_CHANGE_KIND_LABEL[entry.change_kind]} tone={changeTone(entry.change_kind)} />
          <span className="ml-2 font-mono">{entry.key}</span>
        </li>
      ))}
    </ul>
  );
}

function RevisionList({ revisions }: { revisions: RevisionSummary[] }) {
  return (
    <ul className="space-y-1 text-xs" data-testid="ux-revision-history-list">
      {revisions.map((r) => (
        <li key={r.revision_number} className="rounded border p-2">
          <span className="font-mono">#{r.revision_number}</span>
          <StateBadge
            label={REVISION_STATE_LABEL[r.revision_state as keyof typeof REVISION_STATE_LABEL]}
            tone={r.revision_state === "current" ? "outline" : "secondary"}
          />
          <StateBadge
            label={AUTHORSHIP_LABEL[r.authored_by_kind as keyof typeof AUTHORSHIP_LABEL]}
            tone="secondary"
          />
          <span className="ml-2 text-muted-foreground">{formatTimestamp(r.created_at)}</span>
          {r.change_note ? <p className="mt-1 text-muted-foreground">{r.change_note}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function RevisionPicker({
  revisions, from, to, onFrom, onTo,
}: {
  revisions: RevisionSummary[];
  from: number | null;
  to: number | null;
  onFrom: (v: number) => void;
  onTo: (v: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label>
        <span className="mr-1">比較元</span>
        <Select
          aria-label="比較元の版"
          value={from ?? ""}
          onChange={(e) => onFrom(Number(e.target.value))}
        >
          {revisions.map((r) => (
            <option key={r.revision_number} value={r.revision_number}>#{r.revision_number}</option>
          ))}
        </Select>
      </label>
      <label>
        <span className="mr-1">比較先</span>
        <Select
          aria-label="比較先の版"
          value={to ?? ""}
          onChange={(e) => onTo(Number(e.target.value))}
        >
          {revisions.map((r) => (
            <option key={r.revision_number} value={r.revision_number}>#{r.revision_number}</option>
          ))}
        </Select>
      </label>
    </div>
  );
}

/** Journey revision history + revision-to-revision Step diff. */
export function JourneyRevisionHistoryCard({ journeyKey }: { journeyKey: string }) {
  const history = useUxJourneyRevisions(journeyKey);
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const revisions = (history.data?.revisions ?? []) as unknown as RevisionSummary[];
  const hasPair = revisions.length >= 2;
  const effectiveFrom = from ?? (hasPair ? revisions[revisions.length - 2].revision_number : null);
  const effectiveTo = to ?? (hasPair ? revisions[revisions.length - 1].revision_number : null);
  const diff = useUxJourneyRevisionDiff(journeyKey, effectiveFrom, effectiveTo);

  return (
    <div className="space-y-2" data-testid="ux-journey-revision-history">
      <SectionHeading>版の履歴</SectionHeading>
      {history.isLoading ? (
        <LoadingBlock testId="ux-journey-revision-history-loading" />
      ) : history.isError || !history.data ? (
        <LoadErrorCard onRetry={() => history.refetch()} testId="ux-journey-revision-history-error" />
      ) : revisions.length === 0 ? (
        <EmptyNote testId="ux-journey-revision-history-empty">まだ版がありません。</EmptyNote>
      ) : (
        <>
          <RevisionList revisions={revisions} />
          {hasPair ? (
            <>
              <RevisionPicker
                revisions={revisions}
                from={effectiveFrom}
                to={effectiveTo}
                onFrom={setFrom}
                onTo={setTo}
              />
              {diff.isLoading ? (
                <LoadingBlock testId="ux-journey-revision-diff-loading" />
              ) : diff.isError || !diff.data ? (
                <LoadErrorCard onRetry={() => diff.refetch()} testId="ux-journey-revision-diff-error" />
              ) : (
                <DiffEntries
                  state={diff.data.diff_state}
                  entries={diff.data.steps.map((s) => ({ key: s.step_key, change_kind: s.change_kind }))}
                />
              )}
            </>
          ) : (
            <EmptyNote testId="ux-journey-revision-diff-single">
              版が 1 つだけなので、比較できる相手がまだありません。
            </EmptyNote>
          )}
        </>
      )}
    </div>
  );
}

/** Requirement revision history + acceptance-criterion diff. */
export function RequirementRevisionHistoryCard({ requirementKey }: { requirementKey: string }) {
  const history = useUxRequirementRevisions(requirementKey);
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const revisions = (history.data?.revisions ?? []) as unknown as RevisionSummary[];
  const hasPair = revisions.length >= 2;
  const effectiveFrom = from ?? (hasPair ? revisions[revisions.length - 2].revision_number : null);
  const effectiveTo = to ?? (hasPair ? revisions[revisions.length - 1].revision_number : null);
  const diff = useUxRequirementRevisionDiff(requirementKey, effectiveFrom, effectiveTo);

  return (
    <div className="space-y-2" data-testid="ux-requirement-revision-history">
      <SectionHeading>版の履歴</SectionHeading>
      {history.isLoading ? (
        <LoadingBlock testId="ux-requirement-revision-history-loading" />
      ) : history.isError || !history.data ? (
        <LoadErrorCard onRetry={() => history.refetch()} testId="ux-requirement-revision-history-error" />
      ) : revisions.length === 0 ? (
        <EmptyNote testId="ux-requirement-revision-history-empty">まだ版がありません。</EmptyNote>
      ) : (
        <>
          <RevisionList revisions={revisions} />
          {hasPair ? (
            <>
              <RevisionPicker
                revisions={revisions}
                from={effectiveFrom}
                to={effectiveTo}
                onFrom={setFrom}
                onTo={setTo}
              />
              {diff.isLoading ? (
                <LoadingBlock testId="ux-requirement-revision-diff-loading" />
              ) : diff.isError || !diff.data ? (
                <LoadErrorCard onRetry={() => diff.refetch()} testId="ux-requirement-revision-diff-error" />
              ) : (
                <DiffEntries
                  state={diff.data.diff_state}
                  entries={diff.data.criteria.map((c) => ({
                    key: c.criterion_key, change_kind: c.change_kind,
                  }))}
                />
              )}
            </>
          ) : (
            <EmptyNote testId="ux-requirement-revision-diff-single">
              版が 1 つだけなので、比較できる相手がまだありません。
            </EmptyNote>
          )}
        </>
      )}
    </div>
  );
}
