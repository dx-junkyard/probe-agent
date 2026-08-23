// Epic #412 / #414: the read-only Flow explanation.
//
// Six sections (§6.3), each degrading alone (§6.4). Per Node the five axes of
// §1.2 are rendered as FIVE SEPARATE READINGS — there is no combined badge,
// no average, no completion rate and no health score anywhere on this screen
// (ADR-7 / #353). When the SDK policy mode and the execution mode both read
// "shadow", or when only one of them does, both are shown with the sentence
// that keeps them apart: they are two different facts (§1.2).
//
// The client decides none of this. Every axis value, every `*_state`, the
// membership basis, the evidence ids and `degraded_sections` arrive already
// decided by `GET /flow-explanation`.

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  FlowExplanationNodeOut,
  FlowExplanationOut,
  FlowResponsibilitySectionOut,
} from "@/api/types";
import { formatTimestamp } from "@/lib/utils";
import {
  DIVERGENCE_DESCRIPTION,
  DIVERGENCE_LABEL,
  DIVERGENCE_TONE,
  EXECUTION_MODE_DESCRIPTION,
  EXECUTION_MODE_LABEL,
  FACT_STATE_DESCRIPTION,
  MEMBERSHIP_BASIS_LABEL,
  MEMBERSHIP_STATE_LABEL,
  PROPOSAL_STATUS_LABEL,
  SUBJECT_KIND_DESCRIPTION,
  SUBJECT_KIND_SHORT_LABEL,
  SUBJECT_RESOLUTION_LABEL,
  contractReadings,
  describeDependencyReading,
  describeModeOrigin,
  describeShadowReadings,
  externalBoundaryReadings,
  flowEdgeReadings,
  groupOpenItems,
  nodeAxes,
  observationStanding,
  sectionAvailability,
} from "./model";
import {
  EmptyNote,
  FactStateBadge,
  ReadFailure,
  SectionCard,
  StatusBadge,
} from "./shared";

function NodeCard({ node }: { node: FlowExplanationNodeOut }) {
  const axes = nodeAxes(node);
  const shadow = describeShadowReadings(node);
  const origin = describeModeOrigin(node.mode_source, node.mode_reason, node.mode_source_ref);

  return (
    <li className="rounded border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold">{node.node_key}</h3>
        <span className="text-sm">{node.display_name}</span>
        <StatusBadge
          tone="neutral"
          text={node.membership_basis}
          title={MEMBERSHIP_BASIS_LABEL[node.membership_basis]}
        />
      </div>

      <h4 className="mt-3 text-xs font-semibold">5 つの軸（互いから導出しません）</h4>
      <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {axes.map((axis) => (
          <div key={axis.key} className="rounded border p-2">
            <div className="text-xs text-muted-foreground">{axis.label}</div>
            <div className="mt-1 text-sm font-medium">
              {axis.state === "present" && axis.value !== null ? (
                <span className="font-mono">{axis.value}</span>
              ) : (
                <FactStateBadge state={axis.state === "present" ? "missing" : axis.state} />
              )}
            </div>
            {axis.state !== "present" && (
              <div className="mt-1 text-xs text-muted-foreground">
                {FACT_STATE_DESCRIPTION[axis.state]}
              </div>
            )}
            <div className="mt-1 text-xs text-muted-foreground">{axis.hint}</div>
          </div>
        ))}
      </div>

      {shadow.note && (
        <p className="mt-2 rounded border border-dashed p-2 text-xs text-muted-foreground">
          <span className="font-semibold">shadow が 2 か所に出ています: </span>
          {shadow.note}
          <span className="mt-1 block font-mono">
            実行モード = {shadow.executionMode}（
            {EXECUTION_MODE_LABEL[shadow.executionMode]}） / SDK policy mode ={" "}
            {shadow.sdkPolicyMode ?? "(値なし)"}
          </span>
          <span className="mt-1 block">
            {EXECUTION_MODE_DESCRIPTION[shadow.executionMode]}
          </span>
        </p>
      )}

      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <div className="rounded border p-2 text-xs">
          <div className="text-muted-foreground">実行モードの出所</div>
          <div className="mt-1">
            <StatusBadge
              tone={origin.chosen ? "success" : "warning"}
              text={origin.headline}
            />
          </div>
          <div className="mt-1 text-muted-foreground">
            {origin.reasonLabel}
            {origin.sourceRef ? `（${origin.sourceRef}）` : ""}
          </div>
          <div className="mt-1 text-muted-foreground">{origin.nextAction}</div>
          <div className="mt-1 font-mono text-muted-foreground">
            mode_source={node.mode_source} / mode_reason={node.mode_reason}
          </div>
        </div>
        <div className="rounded border p-2 text-xs">
          <div className="text-muted-foreground">設定と実行の突き合わせ</div>
          {node.mode_divergence ? (
            <>
              <div className="mt-1">
                <StatusBadge
                  tone={DIVERGENCE_TONE[node.mode_divergence]}
                  text={`${node.mode_divergence} / ${DIVERGENCE_LABEL[node.mode_divergence]}`}
                />
              </div>
              <div className="mt-1 text-muted-foreground">
                {DIVERGENCE_DESCRIPTION[node.mode_divergence]}
              </div>
              <div className="mt-1 font-mono text-muted-foreground">
                実効 = {node.execution_mode} / 観測 = {node.observed_mode ?? "(観測なし)"}
              </div>
              {/* The observation's STANDING, beside the reading and never
                  folded into it: 「一致している」 and 「実測された」 are two
                  facts (#366). */}
              {observationStanding(node.mode_observation_source, node.mode_observation_run_ref_state) ? (
                <div className="mt-1 text-muted-foreground">
                  観測の裏付け:{" "}
                  {observationStanding(node.mode_observation_source, node.mode_observation_run_ref_state)}
                </div>
              ) : null}
            </>
          ) : (
            <EmptyNote>この Node には突き合わせの読みがありません。</EmptyNote>
          )}
        </div>
      </div>

      <div className="mt-3 rounded border p-2 text-xs">
        <div className="text-muted-foreground">観測カバレッジ</div>
        {node.observation_state === "present" && node.observation ? (
          <div className="mt-1 font-mono">
            state={String((node.observation as Record<string, unknown>).state ?? "")} /{" "}
            sample={String((node.observation as Record<string, unknown>).sample_count ?? "")}
          </div>
        ) : (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <FactStateBadge state={node.observation_state} />
            <span className="text-muted-foreground">
              {FACT_STATE_DESCRIPTION[node.observation_state]}
            </span>
          </div>
        )}
        <div className="mt-1 text-muted-foreground">
          監視契約:{" "}
          {node.monitoring_contract_declared ? "宣言されています" : "宣言されていません"}
        </div>
      </div>

      {node.evidence.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            根拠 {node.evidence.length} 件を表示
          </summary>
          <ul className="mt-2 space-y-1 text-xs">
            {node.evidence.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{item.kind}</Badge>
                <span className="font-mono">{item.id}</span>
                <span className="text-muted-foreground">{item.label}</span>
                {item.recorded_at && (
                  <span className="text-muted-foreground">
                    {formatTimestamp(item.recorded_at)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

/** The Flow's responsibility, I/O boundary and Node dependencies (§6.3 item 2).
 *
 * #414's acceptance criteria ask for 入出力境界・構成 Node と依存関係, so all
 * four of the server's readings are rendered: the ordered Node list, the
 * `edges` dependency table, each Node's recorded input/output contract, and
 * the external boundaries. The rules the rest of the screen follows hold here
 * too — the client classifies nothing, every fact state keeps its own
 * sentence, no value is printed for a state that is not `present`, and an
 * empty list is 「0 件」 ONLY when the reading actually produced one.
 */
function ResponsibilitySection({
  section,
}: {
  section: FlowResponsibilitySectionOut;
}) {
  const reading = describeDependencyReading(section);
  const edges = flowEdgeReadings(section.edges);
  const boundaries = externalBoundaryReadings(section.external_boundaries);
  const contracts = contractReadings(section.contracts);

  return (
    <div className="space-y-4 text-sm">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            tone={
              reading.edgeSource === "unavailable"
                ? "destructive"
                : reading.edgeSource === "not_applicable"
                  ? "neutral"
                  : "success"
            }
            text={`${reading.edgeSource} / ${reading.edgeSourceLabel}`}
          />
          <span className="text-muted-foreground">入口: {section.entry_ref ?? "—"}</span>
          <FactStateBadge state={section.entry_state} />
          {section.truncated && (
            <StatusBadge tone="warning" text="打ち切られています（全件ではありません）" />
          )}
        </div>
        <p className="text-xs text-muted-foreground">{reading.edgeSourceDescription}</p>
      </div>

      <section>
        <h3 className="text-sm font-semibold">構成 Node の並び</h3>
        {section.node_order.length === 0 ? (
          <EmptyNote>並びの記録が 0 件です。</EmptyNote>
        ) : (
          <ol className="mt-1 flex flex-wrap items-center gap-1 font-mono text-xs">
            {section.node_order.map((nodeId, index) => (
              <li key={`${index}:${nodeId}`} className="flex items-center gap-1">
                <span className="rounded border px-1 py-0.5">
                  {index + 1}. {nodeId}
                </span>
                {index < section.node_order.length - 1 && (
                  <span aria-hidden="true">→</span>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold">
          依存関係（辺 {reading.edgesMeasured ? `${edges.length} 件` : "未取得"}）
        </h3>
        {!reading.edgesMeasured ? (
          <p className="mt-1 text-xs text-muted-foreground">
            {reading.edgeSourceDescription}
          </p>
        ) : edges.length === 0 ? (
          <EmptyNote>依存関係の辺が 0 件です。（取得できなかったのではありません）</EmptyNote>
        ) : (
          <div className="mt-1 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">
                {reading.edgeSourceLabel}から読んだ、構成 Node 間の依存関係の一覧
              </caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 pr-2">#</th>
                  <th scope="col" className="py-2 pr-2">呼び出し元</th>
                  <th scope="col" className="py-2 pr-2">呼び出し先</th>
                  <th scope="col" className="py-2 pr-2">種別</th>
                  <th scope="col" className="py-2 pr-2">補足</th>
                </tr>
              </thead>
              <tbody>
                {edges.map((edge) => (
                  <tr key={edge.key} className="border-b align-top">
                    <td className="py-2 pr-2">{edge.position}</td>
                    <td className="py-2 pr-2 font-mono">{edge.source}</td>
                    <td className="py-2 pr-2 font-mono">{edge.target}</td>
                    <td className="py-2 pr-2">{edge.edgeKindLabel}</td>
                    <td className="py-2 pr-2 text-muted-foreground">
                      {edge.detail || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold">入出力境界（Node ごとの契約）</h3>
        {contracts.length === 0 ? (
          <EmptyNote>契約の記録が 0 件です。</EmptyNote>
        ) : (
          <ul className="mt-1 space-y-2">
            {contracts.map((contract) => (
              <li key={contract.nodeKey} className="rounded border p-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono">{contract.nodeKey}</span>
                  <FactStateBadge state={contract.state} />
                </div>
                {contract.state !== "present" ? (
                  // A contract that could not be read is NOT an empty
                  // contract: no field below is rendered for it (#380).
                  <p className="mt-1 text-muted-foreground">
                    {FACT_STATE_DESCRIPTION[contract.state]}
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    <dl className="grid gap-1 sm:grid-cols-2">
                      <div>
                        <dt className="text-muted-foreground">責務</dt>
                        <dd>{contract.mission || "（記録なし）"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">対象範囲</dt>
                        <dd>{contract.scope || "（記録なし）"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">対象外</dt>
                        <dd>{contract.outOfScope || "（記録なし）"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">
                          side effect / trust boundary
                        </dt>
                        <dd className="font-mono">
                          {contract.sideEffectClass ?? "（記録なし）"} /{" "}
                          {contract.trustBoundary ?? "（記録なし）"}
                        </dd>
                      </div>
                    </dl>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {(
                        [
                          ["入力契約", contract.input],
                          ["出力契約", contract.output],
                        ] as const
                      ).map(([label, fields]) => (
                        <div key={label} className="rounded border p-2">
                          <h4 className="font-semibold">{label}</h4>
                          {fields.length === 0 ? (
                            <EmptyNote>記録された項目が 0 件です。</EmptyNote>
                          ) : (
                            <ul className="mt-1 space-y-1 font-mono">
                              {fields.map((field) => (
                                <li key={field.name}>
                                  {field.name}: {field.value}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold">
          外部境界
          {reading.boundariesMeasured ? `（${boundaries.length} 件）` : "（この読みでは測っていません）"}
        </h3>
        {!reading.boundariesMeasured ? (
          // 「0 件」 would claim a measurement that never ran (#366).
          <p className="mt-1 text-xs text-muted-foreground">
            {reading.boundaryUnmeasuredNote}
          </p>
        ) : boundaries.length === 0 ? (
          <EmptyNote>外部境界は 0 件です。（取得できなかったのではありません）</EmptyNote>
        ) : (
          <ul className="mt-1 space-y-1 text-xs">
            {boundaries.map((boundary) => (
              <li
                key={boundary.key}
                className="flex flex-wrap items-center gap-2 rounded border p-2"
              >
                {boundary.boundaryKind ? (
                  <StatusBadge
                    tone="warning"
                    text={`${boundary.boundaryKind} / ${boundary.boundaryKindLabel}`}
                  />
                ) : (
                  <StatusBadge tone="neutral" text="境界種別の記録がありません" />
                )}
                <span className="font-mono">
                  {boundary.qualifiedName || boundary.nodeId}
                </span>
                {boundary.qualifiedName && boundary.nodeId && (
                  <span className="font-mono text-muted-foreground">
                    {boundary.nodeId}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {section.diagnostics.length > 0 && (
        <p className="text-xs text-muted-foreground">
          診断: {section.diagnostics.join(" / ")}
        </p>
      )}
    </div>
  );
}

export function FlowProjectionPanel({
  explanation,
  isLoading,
  isError,
  onRetry,
}: {
  explanation: FlowExplanationOut | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}) {
  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError) {
    return (
      <Card>
        <CardContent className="p-6">
          <ReadFailure what="Flow の説明" onRetry={onRetry} />
        </CardContent>
      </Card>
    );
  }
  if (!explanation) return null;

  const subject = explanation.subject;
  const purpose = sectionAvailability(explanation, "purpose");
  const responsibility = sectionAvailability(explanation, "responsibility");
  const nodes = sectionAvailability(explanation, "nodes");
  const openItems = sectionAvailability(explanation, "open_items");
  const experiments = sectionAvailability(explanation, "experiments");
  const baseline = sectionAvailability(explanation, "baseline");

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle as="h2" className="flex flex-wrap items-center gap-2 text-base">
            <StatusBadge
              tone="neutral"
              text={SUBJECT_KIND_SHORT_LABEL[subject.subject_kind]}
            />
            <span className="font-mono">{subject.subject_ref}</span>
          </CardTitle>
          <CardDescription>
            {SUBJECT_KIND_DESCRIPTION[subject.subject_kind]}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              tone={subject.resolution === "resolved" ? "success" : "warning"}
              text={`${subject.resolution} / ${SUBJECT_RESOLUTION_LABEL[subject.resolution]}`}
            />
            <span className="text-xs text-muted-foreground">
              snapshot: <FactStateBadgeInline state={explanation.subject.snapshot_state} />
              {subject.snapshot_id ? ` #${subject.snapshot_id}` : ""}
              {subject.commit_sha ? ` / commit ${subject.commit_sha.slice(0, 8)}` : ""}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              tone={explanation.membership.state === "resolved" ? "success" : "destructive"}
              text={`${explanation.membership.state} / ${MEMBERSHIP_STATE_LABEL[explanation.membership.state]}`}
            />
            <span className="text-xs text-muted-foreground">
              構成 Node {explanation.membership.node_keys.length} 件
              {explanation.membership.state === "unavailable" &&
                "（0 件という意味ではありません）"}
            </span>
          </div>
          {subject.detail && (
            <p className="text-xs text-muted-foreground">{subject.detail}</p>
          )}
          {explanation.degraded_sections.length > 0 && (
            <p className="text-xs text-muted-foreground">
              取得できなかったセクション: {explanation.degraded_sections.join(", ")}。
              残りのセクションはそのまま表示しています。
            </p>
          )}
        </CardContent>
      </Card>

      <SectionCard
        availability={purpose}
        headingLevel="h2"
        description="この Flow が支えている Purpose 要素 / Capability / Feature。上位の内容は保存せず、参照を読み取り時に解決しています。"
      >
        {explanation.purpose ? (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Purpose Chain:</span>
              <FactStateBadge state={explanation.purpose.purpose_chain_state} />
              {explanation.purpose.detail && (
                <span className="text-xs text-muted-foreground">
                  {explanation.purpose.detail}
                </span>
              )}
            </div>
            {(
              [
                ["Purpose 要素", explanation.purpose.purpose_elements],
                ["Capability", explanation.purpose.capabilities],
                ["Feature", explanation.purpose.features],
              ] as const
            ).map(([label, refs]) => (
              <div key={label}>
                <h3 className="text-xs font-semibold">{label}</h3>
                {refs.length === 0 ? (
                  <EmptyNote>リンクされているものが 0 件です。</EmptyNote>
                ) : (
                  <ul className="mt-1 space-y-1 text-sm">
                    {refs.map((ref) => (
                      <li key={ref.ref} className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs">{ref.ref}</span>
                        <span>{ref.label}</span>
                        {ref.resolution && (
                          <Badge variant="outline">{ref.resolution}</Badge>
                        )}
                        <span className="text-xs text-muted-foreground">
                          Node: {ref.node_keys.join(", ")}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        ) : (
          <EmptyNote>このセクションの内容はありません。</EmptyNote>
        )}
      </SectionCard>

      <SectionCard
        availability={responsibility}
        headingLevel="h2"
        description="責務・入出力境界・構成 Node の依存関係。どの読み（実行時の親子関係か、pin した snapshot の call graph か）かを必ず明示します。"
      >
        {explanation.responsibility ? (
          <ResponsibilitySection section={explanation.responsibility} />
        ) : (
          <EmptyNote>このセクションの内容はありません。</EmptyNote>
        )}
      </SectionCard>

      <SectionCard
        availability={nodes}
        headingLevel="h2"
        description="Node ごとに 5 軸を別々の読みとして表示します。合計・平均・完成度は作りません。"
      >
        {explanation.nodes === null ? (
          <EmptyNote>このセクションの内容はありません。</EmptyNote>
        ) : explanation.nodes.length === 0 ? (
          <EmptyNote>この Flow に紐づく Node が 0 件です。</EmptyNote>
        ) : (
          <ul className="space-y-3">
            {explanation.nodes.map((node) => (
              <NodeCard key={node.node_key} node={node} />
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard
        availability={openItems}
        headingLevel="h2"
        description="現在の anomaly と、missing / unavailable / unmeasured / stale / not_applicable の一覧。5 つは別々の答えです。"
      >
        {explanation.open_items === null ? (
          <EmptyNote>このセクションの内容はありません。</EmptyNote>
        ) : explanation.open_items.items.length === 0 ? (
          <EmptyNote>未解決事項は 0 件です。（取得できなかったのではありません）</EmptyNote>
        ) : (
          <div className="space-y-3">
            {groupOpenItems(explanation.open_items.items).map((group) => (
              <section key={group.kind}>
                <h3 className="text-sm font-semibold">
                  {group.label}（{group.items.length} 件）
                </h3>
                <ul className="mt-1 space-y-1">
                  {group.items.map((item) => (
                    <li key={item.id} className="rounded border p-2 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{item.label}</span>
                        {item.node_key && (
                          <span className="font-mono text-muted-foreground">
                            {item.node_key}
                          </span>
                        )}
                        {item.missing_state && (
                          <FactStateBadge state={item.missing_state} />
                        )}
                      </div>
                      {item.detail && (
                        <div className="mt-1 text-muted-foreground">{item.detail}</div>
                      )}
                      {item.evidence_ids.length > 0 && (
                        <div className="mt-1 font-mono text-muted-foreground">
                          根拠: {item.evidence_ids.join(", ")}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard
        availability={experiments}
        headingLevel="h2"
        description="この Flow に紐づく実験提案の要約です。status は #415 の event fold であり、画面側では導出しません。"
      >
        {explanation.experiments === null ? (
          <EmptyNote>このセクションの内容はありません。</EmptyNote>
        ) : explanation.experiments.proposals.length === 0 ? (
          <EmptyNote>この Flow の実験提案は 0 件です。</EmptyNote>
        ) : (
          <ul className="space-y-1 text-sm">
            {explanation.experiments.proposals.map((proposal) => (
              <li
                key={proposal.proposal_id}
                className="flex flex-wrap items-center gap-2 rounded border p-2"
              >
                <span className="font-mono text-xs">{proposal.proposal_key}</span>
                <span>{proposal.title}</span>
                {proposal.status && proposal.status_state === "present" ? (
                  <StatusBadge
                    tone="neutral"
                    text={`${proposal.status} / ${PROPOSAL_STATUS_LABEL[proposal.status]}`}
                  />
                ) : (
                  <FactStateBadge state={proposal.status_state} />
                )}
                <span className="text-xs text-muted-foreground">
                  対象 Node: {proposal.target_node_keys.join(", ") || "—"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard
        availability={baseline}
        headingLevel="h2"
        description="baseline 実装・rollback 先・人間承認の状態。承認は pin の存在から推測せず、それ自身の記録として読みます。"
      >
        {explanation.baseline === null ? (
          <EmptyNote>このセクションの内容はありません。</EmptyNote>
        ) : explanation.baseline.nodes.length === 0 ? (
          <EmptyNote>対象の Node が 0 件です。</EmptyNote>
        ) : (
          <ul className="space-y-2">
            {explanation.baseline.nodes.map((entry) => (
              <li key={entry.node_key} className="rounded border p-2 text-xs">
                <div className="font-mono text-sm">{entry.node_key}</div>
                <div className="mt-1 grid gap-2 sm:grid-cols-3">
                  <div>
                    <div className="text-muted-foreground">安定実装</div>
                    <FactStateBadge state={entry.stable_state} />
                  </div>
                  <div>
                    <div className="text-muted-foreground">rollback 先</div>
                    <FactStateBadge state={entry.rollback_state} />
                  </div>
                  <div>
                    <div className="text-muted-foreground">人間承認</div>
                    <FactStateBadge state={entry.approval_state} />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}

function FactStateBadgeInline({
  state,
}: {
  state: FlowExplanationOut["subject"]["snapshot_state"];
}) {
  return (
    <span className="inline-flex align-middle">
      <FactStateBadge state={state} />
    </span>
  );
}
