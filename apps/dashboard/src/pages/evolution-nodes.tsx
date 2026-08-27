// Evolution Node inspector (Epic #394 Phase 1, Issue #396).
//
// A deliberately MINIMAL read-only inspector plus the explicit lifecycle
// operations. It is not a lifecycle UX -- Phase 6 (#401) builds the Design
// Studio / Evolution Workbench / Operations Cockpit. Adding a workflow rail
// or a next-action CTA here would create a second, client-side opinion about
// where a Node stands, which is exactly what this Epic exists to prevent.
//
// Three rules govern everything on this page:
//
// 1. The client re-derives nothing. Maturity, the legality of a transition,
//    and every rejection reason come from the server. The page holds no copy
//    of the transition table -- when a transition is refused it renders the
//    server's finite `code` plus the server's message.
// 2. The four axes stay four readings (ADR-6). `maturity`,
//    `improvement_status` and `policy_mode` are shown side by side and never
//    merged into one status word. The fourth axis is absent from the server
//    document entirely, and the page says so rather than showing a blank.
// 3. 「取得できませんでした」(availability === false)、「ありません」(a genuine
//    null) and 「未リンク」 are three different sentences (#380). A value that
//    could not be read is never displayed as zero, empty, or "none".
import { useState } from "react";
import {
  useEvolutionNodes, useEvolutionNode, useEvolutionNodeEvents,
  useCreateEvolutionNode, useTransitionEvolutionNode,
} from "@/api/hooks";
import { ApiError } from "@/api/client";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp } from "@/lib/utils";
import { toast } from "sonner";
import type {
  EvolutionMaturityState, EvolutionNodeProjectionOut, EvolutionNodeEventOut,
} from "@/api/types";

// Canonical state names stay in English (they are the server's vocabulary and
// appear in the API, the event log and the docs); the Japanese gloss is what
// the developer reads. Following docs/ui-glossary.md's 初出のみ併記 rule, both
// are shown wherever the state is the subject of the line.
const MATURITY_LABEL: Record<EvolutionMaturityState, string> = {
  exploring: "探索中",
  validating: "検証中",
  established: "固定化済み",
  monitoring: "監視中",
  reopened: "再探索中",
  suspended: "保留中",
};

const MATURITY_DESCRIPTION: Record<EvolutionMaturityState, string> = {
  exploring: "処理の方式がまだ決まっていない段階です。",
  validating: "実装候補を実データで比較・検証している段階です。",
  established: "固定化の判断が承認され、安定実装が pin されています。",
  monitoring: "固定化済みで、かつ監視契約が実際に観測しています。",
  reopened: "前提が崩れた箇所を再探索中です。本番は安定実装のままです。",
  suspended: "安全のため保留しています。成熟度の段階ではありません。",
};

const MATURITY_VARIANT: Record<
  EvolutionMaturityState, "secondary" | "warning" | "success" | "destructive" | "outline"
> = {
  exploring: "outline",
  validating: "secondary",
  established: "success",
  monitoring: "success",
  reopened: "warning",
  suspended: "destructive",
};

const TRANSITION_TARGETS: EvolutionMaturityState[] = [
  "exploring", "validating", "established", "monitoring", "reopened", "suspended",
];

function MaturityBadge({ state }: { state: EvolutionMaturityState }) {
  return (
    <Badge variant={MATURITY_VARIANT[state]}>
      {state} / {MATURITY_LABEL[state]}
    </Badge>
  );
}

/** One axis reading.
 *
 * `available === false` is 「取得できませんでした」 -- the block could not be
 * read at all. It is deliberately NOT rendered the same as a null value,
 * which means 「未リンク」: nothing of that kind is linked to this Node. A
 * failed read displayed as "none" is the #380 defect (an unreadable fact
 * shown as a fact's default value). */
function AxisReading({
  label, value, available, hint,
}: {
  label: string; value: string | null; available: boolean; hint: string;
}) {
  return (
    <div className="rounded border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm font-medium">
        {!available
          ? <span className="text-destructive">取得できませんでした</span>
          : value === null
            ? <span className="text-muted-foreground">未リンク</span>
            : value}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}

function EventRow({ event }: { event: EvolutionNodeEventOut }) {
  return (
    <li className="border-b py-2 text-sm last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{event.event_kind}</Badge>
        {event.from_state && event.to_state && (
          <span className="font-mono text-xs">
            {event.from_state} → {event.to_state}
          </span>
        )}
        <span className="text-xs text-muted-foreground">
          {formatTimestamp(event.created_at)}
        </span>
      </div>
      <div className="mt-1 text-xs text-muted-foreground">
        実行者: {event.actor ?? "(記録なし)"}（{event.actor_kind}） / 判断方法:{" "}
        {event.decision_method}
        {event.reason ? ` / 理由: ${event.reason}` : ""}
      </div>
      {event.evidence.length > 0 && (
        <div className="mt-1 text-xs text-muted-foreground">
          根拠: {event.evidence.join(", ")}
        </div>
      )}
    </li>
  );
}

function NodeDetail({ projection }: { projection: EvolutionNodeProjectionOut }) {
  const events = useEvolutionNodeEvents(projection.node_id);
  const transition = useTransitionEvolutionNode(projection.node_id);
  const [target, setTarget] = useState<EvolutionMaturityState>("validating");
  const [reason, setReason] = useState("");
  const [rejection, setRejection] = useState<{ code: string; message: string } | null>(null);

  const version = projection.current_version;
  const implementation = projection.current_implementation;

  function submitTransition() {
    setRejection(null);
    transition.mutate(
      { to_state: target, decision_method: "manual", reason },
      {
        onSuccess: (result) => {
          setReason("");
          toast.success(
            result.duplicate
              ? "同じ要求が既に適用済みです（重複）"
              : `maturity を ${result.maturity} に変更しました`,
          );
        },
        onError: (error) => {
          // The server's finite rejection code, shown as-is. The page holds
          // no transition table of its own, so it can neither pre-empt this
          // nor paraphrase it.
          const apiError = error as ApiError;
          setRejection({
            code: apiError.code ?? "unknown",
            message: apiError.detail || "遷移できませんでした",
          });
        },
      },
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{projection.node_key}</span>
            <MaturityBadge state={projection.maturity} />
          </CardTitle>
          <CardDescription>{MATURITY_DESCRIPTION[projection.maturity]}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <h3 className="text-sm font-semibold">4 つの軸</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              これらは互いから導出されません。ある軸の値から別の軸の状態を
              結論づけることはできません。
            </p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <AxisReading
                label="Node maturity"
                value={projection.maturity}
                available
                hint="この Node の契約がどこまで確立しているか"
              />
              <AxisReading
                label="Cell Improvement status"
                value={projection.improvement_status}
                available={projection.availability.improvement_status !== false}
                hint="リンクされた Cell の改善試行の進捗。Node の成熟度ではありません"
              />
              <AxisReading
                label="SDK policy mode"
                value={projection.policy_mode}
                available={projection.availability.policy_mode !== false}
                hint="リンクされた Component の計装モード。shadow は探索中を意味しません"
              />
              <div className="rounded border border-dashed p-3">
                <div className="text-xs text-muted-foreground">
                  Dashboard user phase
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                  この画面では扱いません
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Phase 6（#401）で接続されます。ここで暫定値を出すと
                  「判定済み」に見えてしまうため、表示しません。
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded border p-3">
              <div className="text-xs text-muted-foreground">現在の契約 version</div>
              {projection.availability.version === false ? (
                <div className="mt-1 text-sm text-destructive">取得できませんでした</div>
              ) : version ? (
                <div className="mt-1 space-y-1 text-sm">
                  <div>v{version.version_number}: {version.mission}</div>
                  <div className="text-xs text-muted-foreground">
                    side effect: {version.side_effect_class} / trust boundary:{" "}
                    {version.trust_boundary}
                  </div>
                </div>
              ) : (
                <div className="mt-1 text-sm text-muted-foreground">ありません</div>
              )}
            </div>
            <div className="rounded border p-3">
              <div className="text-xs text-muted-foreground">現在の実装</div>
              {projection.availability.implementation === false ? (
                <div className="mt-1 text-sm text-destructive">取得できませんでした</div>
              ) : implementation ? (
                <div className="mt-1 space-y-1 text-sm">
                  <div className="font-mono">{implementation.modality}</div>
                  <div className="text-xs text-muted-foreground">
                    契約 version #{implementation.node_version_id}
                    {implementation.commit_sha
                      ? ` / commit ${implementation.commit_sha.slice(0, 8)}`
                      : ""}
                  </div>
                </div>
              ) : (
                <div className="mt-1 text-sm text-muted-foreground">ありません</div>
              )}
            </div>
          </div>

          <div className="rounded border p-3 text-sm">
            <div className="text-xs text-muted-foreground">
              安定実装 / rollback 先
            </div>
            <div className="mt-1">
              安定:{" "}
              {projection.stable_implementation
                ? `#${projection.stable_implementation.id}（${projection.stable_implementation.modality}）`
                : "未 pin"}
              {" / "}
              rollback:{" "}
              {projection.rollback_implementation
                ? `#${projection.rollback_implementation.id}`
                : "なし"}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              reopened になっても安定実装の pin は外れません。本番は固定化済みの
              実装を動かしたまま再探索します。
            </p>
          </div>

          <div>
            <h3 className="text-sm font-semibold">リンク</h3>
            {projection.links.length === 0 ? (
              <p className="mt-1 text-sm text-muted-foreground">
                リンクはありません。
              </p>
            ) : (
              <ul className="mt-2 space-y-1 text-sm">
                {projection.links.map((link) => (
                  <li key={link.id} className="flex items-center gap-2">
                    <Badge variant="outline">{link.link_kind}</Badge>
                    <span className="font-mono text-xs">{link.target_ref}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>maturity を変更する</CardTitle>
          <CardDescription>
            すべての遷移は人間の判断（decision_method: manual）として記録されます。
            許可されるかどうかはサーバーが判定します。この画面は遷移表を持ちません。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {TRANSITION_TARGETS.map((state) => (
              <Button
                key={state}
                size="sm"
                variant={state === target ? "default" : "outline"}
                onClick={() => setTarget(state)}
              >
                {state} / {MATURITY_LABEL[state]}
              </Button>
            ))}
          </div>
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="理由（suspended と後退遷移では必須）"
          />
          <Button onClick={submitTransition} disabled={transition.isPending}>
            {transition.isPending ? "送信中…" : "遷移を要求する"}
          </Button>
          {rejection && (
            <div className="rounded border border-destructive p-3 text-sm">
              <div className="font-mono text-xs text-destructive">{rejection.code}</div>
              <div className="mt-1">{rejection.message}</div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>lineage（追記のみ）</CardTitle>
          <CardDescription>
            この記録を畳み込むと現在の maturity が再現されます。保存された値が
            記録からずれていないかを確認できる唯一の手段です。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {events.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : events.isError ? (
            <p className="text-sm text-destructive">
              取得できませんでした。
              <Button
                variant="link"
                size="sm"
                onClick={() => events.refetch()}
              >
                再試行
              </Button>
            </p>
          ) : (
            <ul>
              {(events.data?.events ?? []).map((event) => (
                <EventRow key={event.id} event={event} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function EvolutionNodesPage() {
  const nodes = useEvolutionNodes();
  const createNode = useCreateEvolutionNode();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newKey, setNewKey] = useState("");
  const detail = useEvolutionNode(selectedId);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Evolution Node</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          進化する処理単位の契約・実装方式・成熟度・lineage を確認します。これは
          Phase 1 の読み取り中心の inspector です。設計・探索・運用の導線は
          Phase 6 で統合されます。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Node を作成する</CardTitle>
          <CardDescription>
            node_key は開発者が決める安定した識別子です。component_id からは
            導出しません — Node は Component より先に設計されることも、複数の
            Component にまたがることもあります。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Input
            className="max-w-xs"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="node_key（例: address-normalizer）"
          />
          <Button
            disabled={!newKey.trim() || createNode.isPending}
            onClick={() =>
              createNode.mutate(
                { node_key: newKey.trim() },
                {
                  onSuccess: (node) => {
                    setNewKey("");
                    setSelectedId(node.id);
                    toast.success("Node を作成しました");
                  },
                  onError: (error) =>
                    toast.error((error as ApiError).detail || "作成できませんでした"),
                },
              )
            }
          >
            作成
          </Button>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[320px_1fr] lg:items-start">
        <Card>
          <CardHeader>
            <CardTitle>Node 一覧</CardTitle>
          </CardHeader>
          <CardContent>
            {nodes.isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : nodes.isError ? (
              <p className="text-sm text-destructive">
                取得できませんでした。
                <Button variant="link" size="sm" onClick={() => nodes.refetch()}>
                  再試行
                </Button>
              </p>
            ) : (nodes.data?.nodes ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                この System にはまだ Node がありません。
              </p>
            ) : (
              <ul className="space-y-1">
                {(nodes.data?.nodes ?? []).map((node) => (
                  <li key={node.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(node.id)}
                      className={`w-full rounded border p-2 text-left text-sm ${
                        node.id === selectedId ? "border-primary" : ""
                      }`}
                    >
                      <div className="font-mono">{node.node_key}</div>
                      <div className="mt-1">
                        <MaturityBadge state={node.maturity} />
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <div>
          {selectedId === null ? (
            <Card>
              <CardContent className="p-6 text-sm text-muted-foreground">
                左の一覧から Node を選択してください。
              </CardContent>
            </Card>
          ) : detail.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : detail.isError ? (
            <Card>
              <CardContent className="p-6 text-sm text-destructive">
                Node を取得できませんでした。
                <Button variant="link" size="sm" onClick={() => detail.refetch()}>
                  再試行
                </Button>
              </CardContent>
            </Card>
          ) : detail.data ? (
            <NodeDetail projection={detail.data} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
