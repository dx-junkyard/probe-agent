// Cell Fabric: Root Orchestrator と統合ダイジェスト (Issue #303, Sub 5 of the
// Probe Cell Fabric epic, Issue #297).
//
// Progressive disclosure over app.cell_root.build_root_digest's 4-level
// shape: 結論 (always visible) -> 詳細を表示 expanders for key_points ->
// evidence -> 監査情報. The Ask list lets the developer accept/hold/reject
// a proposed Ask; accepting is explicitly labeled as NOT an execution
// approval (Issue #297's "proposal accept != execution approval" design
// decision -- cell_asks.execution_approved never becomes true here).
import { useState } from "react";
import { Link } from "react-router-dom";
import { useCellRootDigest, useCellAsks, useSyncCellAsks, useDecideCellAsk } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp } from "@/lib/utils";
import { toast } from "sonner";
import type {
  CellAskDigestEntry, CellAskOut, CellRootKeyPoint, CellAskDecision,
} from "@/api/types";

const SEVERITY_BADGE: Record<string, "destructive" | "warning" | "secondary" | "success" | "outline"> = {
  sev1: "destructive",
  sev2: "warning",
  sev3: "secondary",
  error: "destructive",
  blocked: "destructive",
  warning: "warning",
  info: "secondary",
  ok: "success",
};

function SeverityBadge({ severity }: { severity: string }) {
  return <Badge variant={SEVERITY_BADGE[severity] ?? "outline"}>{severity}</Badge>;
}

function EvidenceRefList({ refs, cellId }: { refs: string[]; cellId?: string }) {
  if (!refs.length) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {refs.map((ref) => {
        const [kind, rest] = ref.split(/:(.+)/);
        if (kind === "trace" && cellId) {
          return (
            <Link
              key={ref}
              to={`/components?component=${encodeURIComponent(cellId)}&trace=${encodeURIComponent(rest ?? "")}`}
              className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] underline"
            >
              {ref}
            </Link>
          );
        }
        return (
          <span key={ref} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {ref}
          </span>
        );
      })}
    </div>
  );
}

function RiskEntryRow({ entry }: { entry: CellAskDigestEntry }) {
  return (
    <div className="rounded-lg border p-3 text-sm space-y-1">
      <div className="flex items-center gap-2 flex-wrap">
        <SeverityBadge severity={entry.severity} />
        <span className="font-mono text-xs text-muted-foreground">{entry.cell_id}</span>
        {entry.sources.length > 1 && (
          <Badge variant="outline" className="text-[10px]">{entry.sources.length}件が同一原因に集約</Badge>
        )}
      </div>
      <div>{entry.summary}</div>
      <EvidenceRefList refs={entry.evidence_refs} cellId={entry.cell_id} />
    </div>
  );
}

export default function CellFabricPage() {
  const { data, isLoading, error } = useCellRootDigest();
  const [showKeyPoints, setShowKeyPoints] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const [showAudit, setShowAudit] = useState(false);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Cell Fabric</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Probe Cell群の状態を Root Orchestrator が一本化したダイジェストです。
          結論から順に詳細を展開して確認できます。
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-4">{[1, 2].map(i => <Skeleton key={i} className="h-32 w-full" />)}</div>
      ) : error ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            {String((error as Error).message ?? error)}
          </CardContent>
        </Card>
      ) : !data ? null : (
        <>
          <ConclusionCard
            digest={data.digest}
            showKeyPoints={showKeyPoints}
            onToggleKeyPoints={() => setShowKeyPoints(v => !v)}
          />

          {showKeyPoints && (
            <KeyPointsSection
              keyPoints={data.digest.key_points}
              showEvidence={showEvidence}
              onToggleEvidence={() => setShowEvidence(v => !v)}
              evidence={data.digest.evidence}
              showAudit={showAudit}
              onToggleAudit={() => setShowAudit(v => !v)}
              audit={data.digest.audit}
            />
          )}
        </>
      )}

      <AskListSection />
    </div>
  );
}

function ConclusionCard({ digest, showKeyPoints, onToggleKeyPoints }: {
  digest: NonNullable<ReturnType<typeof useCellRootDigest>["data"]>["digest"];
  showKeyPoints: boolean;
  onToggleKeyPoints: () => void;
}) {
  const { overall_severity, counts, top_risks } = digest.conclusion;
  return (
    <Card data-testid="cell-fabric-conclusion">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 flex-wrap">
          結論
          <SeverityBadge severity={overall_severity} />
        </CardTitle>
        <CardDescription>
          オーケストレーター {counts.orchestrators}件 ·
          未対応Ask {counts.asks_open}件 ·
          sev1 {counts.escalations_open_by_severity.sev1 ?? 0}件 ·
          sev2 {counts.escalations_open_by_severity.sev2 ?? 0}件 ·
          sev3 {counts.escalations_open_by_severity.sev3 ?? 0}件
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {top_risks.length === 0 ? (
          <div className="text-sm text-muted-foreground">重大なリスク(sev1)はありません。</div>
        ) : (
          <div className="space-y-2">
            <div className="text-sm font-medium">最重要リスク(最大3件)</div>
            {top_risks.map(risk => <RiskEntryRow key={risk.dedupe_key} entry={risk} />)}
          </div>
        )}
        <Button size="sm" variant="outline" onClick={onToggleKeyPoints} data-testid="cell-fabric-detail-toggle">
          {showKeyPoints ? "詳細を隠す" : "詳細を表示"}
        </Button>
      </CardContent>
    </Card>
  );
}

function KeyPointsSection({
  keyPoints, showEvidence, onToggleEvidence, evidence, showAudit, onToggleAudit, audit,
}: {
  keyPoints: CellRootKeyPoint[];
  showEvidence: boolean;
  onToggleEvidence: () => void;
  evidence: NonNullable<ReturnType<typeof useCellRootDigest>["data"]>["digest"]["evidence"];
  showAudit: boolean;
  onToggleAudit: () => void;
  audit: NonNullable<ReturnType<typeof useCellRootDigest>["data"]>["digest"]["audit"];
}) {
  return (
    <Card data-testid="cell-fabric-key-points">
      <CardHeader>
        <CardTitle className="text-base">詳細</CardTitle>
        <CardDescription>オーケストレーター単位の進捗/品質/ボトルネックと、判断待ちの項目です。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {keyPoints.length === 0 ? (
          <div className="text-sm text-muted-foreground">表示する項目はありません。</div>
        ) : (
          keyPoints.map((kp, i) =>
            kp.type === "orchestrator" ? (
              <div key={`orch-${kp.cell_id}-${i}`} className="rounded-lg border p-3 text-sm space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant="outline">Orchestrator</Badge>
                  <span className="font-mono text-xs">{kp.cell_id}</span>
                  {kp.binding_stale && <Badge variant="warning">binding stale</Badge>}
                </div>
                <div className="text-xs text-muted-foreground">
                  {Object.entries(kp.progress.total).map(([status, n]) => `${status}: ${n}`).join(" / ")}
                </div>
                {(kp.quality ?? []).length > 0 && (
                  <div className="space-y-1 pt-1">
                    <div className="text-xs font-medium">品質</div>
                    {(kp.quality ?? []).map(q => (
                      <div key={q.cell_id} className="text-xs text-muted-foreground">
                        <span className="font-mono">{q.cell_id}</span>
                        {" · "}pass rate {q.pass_rate == null ? "-" : `${Math.round(q.pass_rate * 100)}%`}
                        {" · "}audits {q.audited_count == null ? "-" : q.audited_count}
                        {q.intake_status === "suspended" && (
                          <Badge variant="destructive" className="ml-2 text-[10px]">受付停止</Badge>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {(kp.topology ?? []).some(t =>
                  t.feature_refs.length || t.capability_refs.length || t.entrypoint_refs.length
                ) && (
                  <div className="space-y-1 pt-1">
                    <div className="text-xs font-medium">Topology → Cell</div>
                    {(kp.topology ?? []).map(t => {
                      const refs = [
                        ...t.feature_refs.map(ref => ({
                          label: `Feature:${ref}`,
                          to: `/feature-map?feature=${encodeURIComponent(ref)}`,
                        })),
                        ...t.capability_refs.map(ref => ({
                          label: `Capability:${ref}`,
                          to: `/capability-map?capability=${encodeURIComponent(ref)}`,
                        })),
                        ...t.entrypoint_refs.map(ref => ({
                          label: `Entrypoint:${ref}`,
                          // Binding refs intentionally carry no guessed
                          // entrypoint type, so open the canonical explorer
                          // rather than fabricating an auto-selection query.
                          to: "/flow-explorer",
                        })),
                      ];
                      if (!refs.length) return null;
                      return (
                        <div key={t.cell_id} className="text-xs">
                          <span className="font-mono text-muted-foreground">{t.cell_id}</span>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {refs.map(ref => (
                              <Link
                                key={ref.label}
                                to={ref.to}
                                aria-label={`${ref.label} を開く`}
                              >
                                <Badge variant="outline" className="text-[10px]">{ref.label}</Badge>
                              </Link>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                {kp.bottleneck_candidates.length > 0 && (
                  <div className="text-xs">
                    ボトルネック候補: {kp.bottleneck_candidates.length}件
                  </div>
                )}
              </div>
            ) : (
              <div key={`sev2-${kp.dedupe_key}`}>
                <div className="mb-1 text-xs font-medium text-muted-foreground">判断待ち(sev2)</div>
                <RiskEntryRow entry={kp} />
              </div>
            ),
          )
        )}

        <Button size="sm" variant="outline" onClick={onToggleEvidence} data-testid="cell-fabric-evidence-toggle">
          {showEvidence ? "エビデンスを隠す" : "エビデンスを見る"}
        </Button>
        {showEvidence && (
          <div className="space-y-3 rounded-lg border p-3" data-testid="cell-fabric-evidence">
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">詳細情報のみ(sev3)</div>
              {evidence.sev3_escalations.length === 0 ? (
                <div className="text-xs text-muted-foreground">なし</div>
              ) : (
                <div className="space-y-2">
                  {evidence.sev3_escalations.map(e => <RiskEntryRow key={e.dedupe_key} entry={e} />)}
                </div>
              )}
            </div>
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">Taskのエビデンス</div>
              {evidence.tasks.length === 0 ? (
                <div className="text-xs text-muted-foreground">なし</div>
              ) : (
                <div className="space-y-1">
                  {evidence.tasks.map(t => (
                    <div key={t.task_id} className="text-xs">
                      <span className="font-mono">Task #{t.task_id}</span>
                      <span className="text-muted-foreground"> ({t.cell_id})</span>
                      <EvidenceRefList refs={t.evidence_refs} cellId={t.cell_id} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <Button size="sm" variant="outline" onClick={onToggleAudit} data-testid="cell-fabric-audit-toggle">
          {showAudit ? "監査情報を隠す" : "監査情報を表示"}
        </Button>
        {showAudit && (
          <div className="space-y-1 rounded-lg border p-3 text-xs text-muted-foreground" data-testid="cell-fabric-audit">
            <div>System State生成時刻: {formatTimestamp(audit.system_state.generated_at)}</div>
            <div>user_phase: {audit.system_state.user_phase ?? "-"}</div>
            <div>overall_severity: {audit.system_state.overall_severity}</div>
            {audit.is_stale && (
              <div className="text-amber-600 dark:text-amber-400">
                stale — {audit.stale_orchestrator_cell_ids.join(", ") || "System State側でstaleを検出"}
              </div>
            )}
            <div>{audit.decision_method_notes}</div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AskListSection() {
  const { data, isLoading } = useCellAsks("open");
  const sync = useSyncCellAsks();
  const decide = useDecideCellAsk();
  const asks = data?.asks ?? [];

  const handleDecide = (ask: CellAskOut, decision: CellAskDecision) => {
    decide.mutateAsync({ askId: ask.id, decision })
      .then(() => toast.success("Askを更新しました"))
      .catch(e => toast.error(String(e)));
  };

  return (
    <Card data-testid="cell-fabric-asks">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">Ask一覧</CardTitle>
            <CardDescription>
              承認は提案の受け入れのみを意味し、実行には別途承認が必要です。
            </CardDescription>
          </div>
          <Button
            size="sm" variant="outline" disabled={sync.isPending}
            onClick={() =>
              sync.mutateAsync()
                .then(r => toast.success(`同期しました(新規${r.created}件 / 重複${r.deduped}件)`))
                .catch(e => toast.error(String(e)))
            }
          >
            {sync.isPending ? "同期中..." : "Askを同期"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : asks.length === 0 ? (
          <div className="text-sm text-muted-foreground">未対応のAskはありません。</div>
        ) : (
          asks.map(ask => (
            <div key={ask.id} className="rounded-lg border p-3 text-sm space-y-2" data-testid={`cell-ask-${ask.id}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <SeverityBadge severity={ask.severity} />
                <Badge variant="outline" className="text-[10px]">{ask.source_kind}</Badge>
                {ask.cell_definition_id && (
                  <span className="font-mono text-xs text-muted-foreground">{ask.cell_definition_id}</span>
                )}
              </div>
              <div>{ask.ask_text}</div>
              <div className="flex gap-2">
                <Button
                  size="sm" variant="outline" disabled={decide.isPending}
                  onClick={() => handleDecide(ask, "accepted")}
                >
                  承認
                </Button>
                <Button
                  size="sm" variant="outline" disabled={decide.isPending}
                  onClick={() => handleDecide(ask, "held")}
                >
                  保留
                </Button>
                <Button
                  size="sm" variant="outline" disabled={decide.isPending}
                  onClick={() => handleDecide(ask, "rejected")}
                >
                  却下
                </Button>
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
