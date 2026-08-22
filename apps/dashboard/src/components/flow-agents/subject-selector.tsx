// Epic #412 / #414: choosing the Flow subject to explain.
//
// The two kinds are two lists and never one. A `runtime_flow` is an observed
// `trace_spans.flow_id`; a `static_flow` is a pinned snapshot's
// `code_entrypoints.entrypoint_id`. They answer different questions, only the
// runtime kind can be an execution-mode scope (EM-ADR-1), and merging them
// into one "flows" list is exactly the one-word-two-facts collapse §2.1
// forbids.

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  FlowRuntimeSubjectOut,
  FlowStaticSubjectOut,
  FlowSubjectKind,
  FlowSubjectListOut,
} from "@/api/types";
import { formatTimestamp } from "@/lib/utils";
import {
  FACT_STATE_DESCRIPTION,
  FACT_STATE_LABEL,
  SUBJECT_KIND_SHORT_LABEL,
  subjectGroups,
} from "./model";
import { EmptyNote, FactStateBadge, ReadFailure, StatusBadge } from "./shared";

function isRuntime(
  item: FlowRuntimeSubjectOut | FlowStaticSubjectOut,
): item is FlowRuntimeSubjectOut {
  return item.subject_kind === "runtime_flow";
}

export function FlowSubjectSelector({
  listing,
  isLoading,
  isError,
  onRetry,
  selectedKind,
  selectedRef,
  onSelect,
}: {
  listing: FlowSubjectListOut | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  selectedKind: FlowSubjectKind | null;
  selectedRef: string | null;
  onSelect: (kind: FlowSubjectKind, ref: string) => void;
}) {
  const groups = subjectGroups(listing);

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2" className="text-base">
          Flow を選ぶ
        </CardTitle>
        <CardDescription>
          runtime_flow と static_flow は別の種別です。ひとつの「Flow 一覧」には
          まとめません。実行モードのスコープになれるのは runtime_flow だけです。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : isError ? (
          <ReadFailure what="Flow 一覧" onRetry={onRetry} />
        ) : (
          <>
            <div className="text-xs text-muted-foreground">
              static_flow の pin: snapshot{" "}
              {listing?.snapshot_state === "present" ? (
                `#${listing?.snapshot_id}`
              ) : (
                <span title={FACT_STATE_DESCRIPTION[listing?.snapshot_state ?? "missing"]}>
                  {listing?.snapshot_state ?? "missing"} /{" "}
                  {FACT_STATE_LABEL[listing?.snapshot_state ?? "missing"]}
                </span>
              )}
            </div>
            {groups.map((group) => (
              <section key={group.kind} aria-labelledby={`flow-subject-${group.kind}`}>
                <h3
                  id={`flow-subject-${group.kind}`}
                  className="text-sm font-semibold"
                >
                  {group.label}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">{group.description}</p>
                {group.items === null ? (
                  <div className="mt-2 rounded border border-destructive p-3 text-sm">
                    <div className="font-medium text-destructive">
                      この一覧は取得できませんでした
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      「該当なし」ではありません。推測値は入れません。
                    </p>
                    {group.degradedDetail && (
                      <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                        {group.degradedDetail}
                      </p>
                    )}
                  </div>
                ) : group.items.length === 0 ? (
                  <div className="mt-2">
                    <EmptyNote>
                      この System には {SUBJECT_KIND_SHORT_LABEL[group.kind]} が 0 件です。
                    </EmptyNote>
                  </div>
                ) : (
                  <ul className="mt-2 space-y-1">
                    {group.items.map((item) => {
                      const selected =
                        selectedKind === item.subject_kind &&
                        selectedRef === item.subject_ref;
                      return (
                        <li key={`${item.subject_kind}:${item.subject_ref}`}>
                          <button
                            type="button"
                            aria-pressed={selected}
                            onClick={() => onSelect(item.subject_kind, item.subject_ref)}
                            className={`w-full rounded border p-2 text-left text-sm ${
                              selected ? "border-primary" : ""
                            }`}
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <StatusBadge
                                tone="neutral"
                                text={SUBJECT_KIND_SHORT_LABEL[item.subject_kind]}
                              />
                              <span className="font-mono text-xs">{item.subject_ref}</span>
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground">
                              {isRuntime(item)
                                ? `${item.label} / trace ${item.trace_count} 件 / 最終 ${formatTimestamp(item.last_at)} / リンク済み Node ${item.linked_node_count} 件`
                                : `${item.label} / ${item.entrypoint_type ?? "種別不明"} / ${item.handler_path ?? "パス不明"}`}
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            ))}
            {(listing?.degraded_sections.length ?? 0) > 0 && (
              <p className="text-xs text-muted-foreground">
                取得できなかった一覧: {listing?.degraded_sections.join(", ")}。
                他の一覧は通常どおり表示しています。
              </p>
            )}
            {listing?.snapshot_state !== "present" && (
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <FactStateBadge state={listing?.snapshot_state ?? "missing"} />
                <span>
                  static_flow は pin した snapshot が無いと列挙できません。
                </span>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
