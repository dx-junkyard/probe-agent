// Issue #409: Solution Design list -> Options / Requirement links / Target
// links / decisions -> the read-only handoff (§4.2 level 4: "採用案と実装対
// 象"). §3.6 governs every piece of copy here: an `adopt` decision changes
// only `solution_design_decision` and the Option's own `option_status` --
// never `evolution_node.maturity`, Cell Improvement status, SDK policy mode,
// a patch, a worktree write, or a Probe Plan approval. No sentence on this
// screen may imply otherwise.

import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  useSolutionDesigns, useSolutionDesignDetail, useCreateSolutionDesign,
  useAddSolutionDesignOption, useAddSolutionDesignRequirementLink,
  useAddSolutionDesignTargetLink, useRecordSolutionDesignDecision,
  useSolutionDesignChangeOrigins, useSolutionDesignHandoff, useUxRequirements,
} from "@/api/hooks";
import type { SolutionDesignOptionDecision, SolutionTargetKind } from "@/api/types";
import {
  CHANGE_ORIGIN_LABEL, HANDOFF_STATE_LABEL, LINK_STALE_REASON_LABEL, LINK_STATE_LABEL,
  OPTION_DECISION_LABEL, OPTION_STATUS_LABEL, TARGET_KIND_LABEL,
  evaluationPolicyGroups, sortSolutionDesigns,
} from "./model";
import {
  DegradedNote, EmptyNote, LoadErrorCard, LoadingBlock, SectionHeading, StateBadge,
} from "./shared";

const TARGET_KINDS: SolutionTargetKind[] = [
  "capability", "static_flow", "runtime_flow", "evolution_node",
  "component", "cell_definition", "cell_binding", "probe_point",
];
const OPTION_DECISIONS: SolutionDesignOptionDecision[] = ["adopt", "hold", "reject", "withdraw"];

function SolutionDesignCreateForm() {
  const create = useCreateSolutionDesign();
  const [key, setKey] = useState("");
  const [title, setTitle] = useState("");

  function submit() {
    create.mutate(
      { design_key: key.trim(), title },
      {
        onSuccess: () => {
          setKey("");
          setTitle("");
          toast.success("Solution Design を作成しました");
        },
        onError: (error) => toast.error((error as ApiError).detail || "作成できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-solution-design-create-form">
      <Input placeholder="design_key" value={key} onChange={(e) => setKey(e.target.value)} />
      <Input placeholder="タイトル(任意)" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Button size="sm" disabled={!key.trim() || create.isPending} onClick={submit}>
        {create.isPending ? "作成中…" : "Solution Design を作成する"}
      </Button>
    </div>
  );
}

function SolutionDesignList({
  selectedKey,
  onSelect,
}: {
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const designs = useSolutionDesigns();

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Solution Design 一覧</CardTitle>
        <CardDescription>Requirement を満たす実現案。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <SolutionDesignCreateForm />
        {designs.isLoading ? (
          <LoadingBlock testId="ux-solution-design-list-loading" />
        ) : designs.isError ? (
          <LoadErrorCard onRetry={() => designs.refetch()} testId="ux-solution-design-list-error" />
        ) : (designs.data?.designs ?? []).length === 0 ? (
          <EmptyNote testId="ux-solution-design-list-empty">
            この System にはまだ Solution Design がありません。
          </EmptyNote>
        ) : (
          <>
            <DegradedNote
              sections={designs.data?.degraded_sections ?? []}
              detail={designs.data?.degraded_detail ?? {}}
            />
            <ul className="space-y-1" data-testid="ux-solution-design-list">
              {sortSolutionDesigns(designs.data?.designs ?? []).map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    data-testid={`ux-solution-design-item-${d.design_key}`}
                    onClick={() => onSelect(d.design_key)}
                    className={`w-full rounded border p-2 text-left text-sm ${
                      d.design_key === selectedKey ? "border-primary" : ""
                    }`}
                  >
                    <div className="font-mono">{d.design_key}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      Option {d.option_count} 件
                      {d.adopted_option_key ? ` / 採用済み: ${d.adopted_option_key}` : " / 未採用"}
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

function AddOptionForm({
  designKey,
  nextOrder,
  onDone,
}: {
  designKey: string;
  nextOrder: number;
  onDone: () => void;
}) {
  const add = useAddSolutionDesignOption(designKey);
  const [optionKey, setOptionKey] = useState("");
  const [title, setTitle] = useState("");
  const [approach, setApproach] = useState("");
  const [tradeoffs, setTradeoffs] = useState("");
  const [risks, setRisks] = useState("");

  function submit() {
    add.mutate(
      { option_key: optionKey.trim(), option_order: nextOrder, title, approach, tradeoffs, risks },
      {
        onSuccess: () => {
          toast.success("Option を追加しました");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="ux-solution-design-add-option-form">
      <Input placeholder="option_key" value={optionKey} onChange={(e) => setOptionKey(e.target.value)} />
      <Input placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea placeholder="アプローチ(approach)" value={approach} onChange={(e) => setApproach(e.target.value)} rows={2} />
      <Textarea placeholder="トレードオフ" value={tradeoffs} onChange={(e) => setTradeoffs(e.target.value)} rows={2} />
      <Textarea placeholder="リスク" value={risks} onChange={(e) => setRisks(e.target.value)} rows={2} />
      <Button size="sm" disabled={!optionKey.trim() || add.isPending} onClick={submit}>
        {add.isPending ? "追加中…" : "Option を追加する"}
      </Button>
    </div>
  );
}

function AddRequirementLinkForm({ designKey, onDone }: { designKey: string; onDone: () => void }) {
  const requirements = useUxRequirements();
  const add = useAddSolutionDesignRequirementLink(designKey);
  const [requirementKey, setRequirementKey] = useState("");
  const [note, setNote] = useState("");

  function submit() {
    add.mutate(
      { requirement_key: requirementKey, note },
      {
        onSuccess: () => {
          toast.success("Requirement への紐づけを追加しました");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="ux-solution-design-add-requirement-link-form">
      <Select value={requirementKey} onChange={(e) => setRequirementKey(e.target.value)}>
        <option value="">Requirement を選択</option>
        {(requirements.data?.requirements ?? []).map((r) => (
          <option key={r.requirement_key} value={r.requirement_key}>
            {r.requirement_key}
          </option>
        ))}
      </Select>
      <Input placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" disabled={!requirementKey || add.isPending} onClick={submit}>
        {add.isPending ? "追加中…" : "紐づける"}
      </Button>
    </div>
  );
}

function AddTargetLinkForm({
  designKey,
  optionKeys,
  onDone,
}: {
  designKey: string;
  optionKeys: string[];
  onDone: () => void;
}) {
  const add = useAddSolutionDesignTargetLink(designKey);
  const [optionKey, setOptionKey] = useState(optionKeys[0] ?? "");
  const [targetKind, setTargetKind] = useState<SolutionTargetKind>("capability");
  const [targetRef, setTargetRef] = useState("");
  const [snapshotId, setSnapshotId] = useState("");
  const [note, setNote] = useState("");

  function submit() {
    add.mutate(
      {
        option_key: optionKey,
        target_kind: targetKind,
        target_ref: targetRef.trim(),
        captured_snapshot_id: targetKind === "static_flow" && snapshotId ? Number(snapshotId) : undefined,
        note,
      },
      {
        onSuccess: () => {
          toast.success("実装対象への紐づけを追加しました");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "追加できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="ux-solution-design-add-target-link-form">
      <Select value={optionKey} onChange={(e) => setOptionKey(e.target.value)}>
        {optionKeys.map((k) => (
          <option key={k} value={k}>
            {k}
          </option>
        ))}
      </Select>
      <Select value={targetKind} onChange={(e) => setTargetKind(e.target.value as SolutionTargetKind)}>
        {TARGET_KINDS.map((k) => (
          <option key={k} value={k}>
            {TARGET_KIND_LABEL[k]}
          </option>
        ))}
      </Select>
      <Input placeholder="target_ref" value={targetRef} onChange={(e) => setTargetRef(e.target.value)} />
      {targetKind === "static_flow" && (
        <Input
          placeholder="captured_snapshot_id(静的 Flow に必須)"
          value={snapshotId}
          onChange={(e) => setSnapshotId(e.target.value)}
        />
      )}
      <Input placeholder="メモ(任意)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" disabled={!optionKey || !targetRef.trim() || add.isPending} onClick={submit}>
        {add.isPending ? "追加中…" : "紐づける"}
      </Button>
    </div>
  );
}

/**
 * Outbound deep links from the studio back to the target's own screen
 * (§4.1's "UX Design Studio -> 各 target へ戻る deep link" row). Every one
 * of these NAVIGATES only (#358) -- it never runs an action.
 *
 * `capability` links with the resolved name because Capability Map's own
 * `?capability=` param matches by name or `capability_key`, never by the
 * `understanding_capability_entity.id` this layer stores as `target_ref`
 * (§3.3) -- an id-based link there would silently fail to auto-select.
 * `static_flow` / `runtime_flow` / `evolution_node` land on their screens
 * without a pre-selection: neither screen's search params match this
 * layer's `target_ref` format closely enough to guess correctly, and a
 * link that LOOKS deep but selects the wrong thing is worse than a plain
 * navigate.
 */
function targetDeepLink(
  kind: SolutionTargetKind,
  targetName: string | null,
): { to: string; label: string } | null {
  switch (kind) {
    case "capability":
      return targetName
        ? { to: `/capability-map?capability=${encodeURIComponent(targetName)}`, label: "Capability Map で見る" }
        : null;
    case "static_flow":
    case "runtime_flow":
      return { to: "/flow-explorer", label: "Flow Explorer を開く" };
    case "evolution_node":
      return { to: "/evolution-nodes", label: "Evolution Node を開く" };
    default:
      return null;
  }
}

function OptionDecisionControls({ designKey, optionKeys }: { designKey: string; optionKeys: string[] }) {
  const record = useRecordSolutionDesignDecision(designKey);
  const [optionKey, setOptionKey] = useState(optionKeys[0] ?? "");
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<{ code: string; message: string } | null>(null);

  function submit(decision: SolutionDesignOptionDecision) {
    setRejection(null);
    record.mutate(
      { option_key: optionKey, decision, rationale },
      {
        onSuccess: () => {
          setRationale("");
          toast.success(`「${OPTION_DECISION_LABEL[decision]}」を記録しました(実装への適用は行われません)`);
        },
        onError: (error) => {
          const apiError = error as ApiError;
          setRejection({ code: apiError.code ?? "unknown", message: apiError.detail || "記録できませんでした" });
        },
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-solution-design-option-decisions">
      <SectionHeading>Option の採否(人間の判断として記録されます。実装への適用は行われません)</SectionHeading>
      <Select value={optionKey} onChange={(e) => setOptionKey(e.target.value)}>
        {optionKeys.map((k) => (
          <option key={k} value={k}>
            {k}
          </option>
        ))}
      </Select>
      <Textarea placeholder="理由(任意)" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
      <div className="flex flex-wrap gap-2">
        {OPTION_DECISIONS.map((d) => (
          <Button key={d} size="sm" variant="outline" disabled={!optionKey || record.isPending} onClick={() => submit(d)}>
            {OPTION_DECISION_LABEL[d]}
          </Button>
        ))}
      </div>
      {rejection && (
        <div className="rounded border border-destructive p-2 text-xs" data-testid="ux-solution-design-decision-rejected">
          <span className="font-mono text-destructive">{rejection.code}</span>
          <span className="ml-2">{rejection.message}</span>
        </div>
      )}
    </div>
  );
}

function ChangeOriginsSection({ designKey }: { designKey: string }) {
  const origins = useSolutionDesignChangeOrigins(designKey);
  return (
    <div className="space-y-2" data-testid="ux-solution-design-change-origins">
      <SectionHeading>何が変わったために古くなったか</SectionHeading>
      {origins.isLoading ? (
        <LoadingBlock testId="ux-solution-design-change-origins-loading" />
      ) : origins.isError || !origins.data ? (
        <LoadErrorCard onRetry={() => origins.refetch()} testId="ux-solution-design-change-origins-error" />
      ) : origins.data.origins.length === 0 ? (
        <EmptyNote>現在 current でない参照はありません。</EmptyNote>
      ) : (
        <ul className="space-y-1 text-xs">
          {origins.data.origins.map((o, i) => (
            <li key={i} className="rounded border p-2">
              <StateBadge label={CHANGE_ORIGIN_LABEL[o.origin]} tone="warning" />
              <span className="ml-2">{o.detail}</span>
              {o.stale_reason && (
                <span className="ml-2 text-muted-foreground">({LINK_STALE_REASON_LABEL[o.stale_reason]})</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The read-only handoff (§3.7 / §4.2 level 4). "Adopted, never applied"
 * (§3.6) is stated explicitly rather than left implicit. */
function HandoffSection({ designKey }: { designKey: string }) {
  const handoff = useSolutionDesignHandoff(designKey);

  if (handoff.isLoading) return <LoadingBlock testId="ux-solution-design-handoff-loading" />;
  if (handoff.isError || !handoff.data) {
    return <LoadErrorCard onRetry={() => handoff.refetch()} testId="ux-solution-design-handoff-error" />;
  }
  const h = handoff.data;
  const groups = evaluationPolicyGroups(h.evaluation_policy_refs);

  return (
    <div className="space-y-3" data-testid="ux-solution-design-handoff">
      <div className="flex items-center gap-2">
        <SectionHeading>採用案と実装対象(handoff)</SectionHeading>
        <StateBadge
          label={HANDOFF_STATE_LABEL[h.handoff_state]}
          tone={h.handoff_state === "complete" ? "success" : h.handoff_state === "incomplete" ? "warning" : "destructive"}
        />
      </div>

      {h.adopted_option ? (
        <div className="rounded border p-3 text-sm" data-testid="ux-solution-design-adopted-option">
          <div className="font-medium">{h.adopted_option.title || h.adopted_option.option_key}</div>
          <p className="mt-1 text-muted-foreground">{h.adopted_option.approach}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            採用されています。この記録は実装への適用・デプロイ・publish を一切行いません。
          </p>
        </div>
      ) : (
        <EmptyNote testId="ux-solution-design-no-adopted-option">
          まだ採用された Option がありません。
        </EmptyNote>
      )}

      <div>
        <SectionHeading as="h4">実装対象</SectionHeading>
        {h.target_links.length === 0 ? (
          <EmptyNote>実装対象への紐づけはありません。</EmptyNote>
        ) : (
          <ul className="mt-1 space-y-1 text-xs">
            {h.target_links.map((l) => {
              const deepLink = targetDeepLink(l.target_kind, l.target_name);
              return (
                <li key={l.id} className="rounded border p-2">
                  <StateBadge label={TARGET_KIND_LABEL[l.target_kind]} />
                  <span className="ml-2 font-mono">{l.target_name ?? l.target_ref}</span>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <StateBadge
                      label={LINK_STATE_LABEL[l.link_state]}
                      tone={l.link_state === "current" ? "outline" : l.review_required ? "warning" : "destructive"}
                    />
                    {deepLink && (
                      <Link
                        to={deepLink.to}
                        className="text-primary underline underline-offset-2"
                        data-testid={`ux-solution-design-target-deep-link-${l.id}`}
                      >
                        {deepLink.label}
                      </Link>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div>
        <SectionHeading as="h4">紐づく Requirement</SectionHeading>
        {h.requirements.length === 0 ? (
          <EmptyNote>紐づく Requirement はありません。</EmptyNote>
        ) : (
          <ul className="mt-1 space-y-1 text-xs">
            {h.requirements.map((r) => (
              <li key={r.id} className="font-mono">
                {r.requirement_key}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-3" data-testid="ux-solution-design-evaluation-groups">
        <SectionHeading as="h4">評価(level ごとに分けて表示。合成しません)</SectionHeading>
        {groups.map((g) => (
          <div key={g.level} className="rounded border p-2" data-testid={`ux-solution-design-evaluation-${g.level}`}>
            <div className="text-xs font-semibold">{g.label}</div>
            {g.items.length === 0 ? (
              <EmptyNote testId={`ux-solution-design-evaluation-${g.level}-empty`}>
                この level の評価方針はまだありません。
              </EmptyNote>
            ) : (
              <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                {g.items.map((item, i) => (
                  <li key={i}>{JSON.stringify(item)}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      {h.unresolved_references.length > 0 && (
        <div className="rounded border border-amber-400/50 p-2 text-xs" data-testid="ux-solution-design-unresolved-refs">
          <div className="font-semibold">解決できなかった参照</div>
          <ul className="mt-1 space-y-1">
            {h.unresolved_references.map((u, i) => (
              <li key={i}>
                {u.kind} / {u.ref}: {u.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function SolutionDesignDetail({ designKey }: { designKey: string }) {
  const detail = useSolutionDesignDetail(designKey);
  const [optionFormOpen, setOptionFormOpen] = useState(false);
  const [reqLinkOpen, setReqLinkOpen] = useState(false);
  const [targetLinkOpen, setTargetLinkOpen] = useState(false);

  if (detail.isLoading) return <LoadingBlock testId="ux-solution-design-detail-loading" />;
  if (detail.isError || !detail.data) {
    return <LoadErrorCard onRetry={() => detail.refetch()} testId="ux-solution-design-detail-error" />;
  }
  const d = detail.data;
  const optionKeys = d.options.filter((o) => o.superseded_by_id === null).map((o) => o.option_key);

  return (
    <div className="space-y-4" data-testid="ux-solution-design-detail">
      <DegradedNote sections={d.degraded_sections} detail={d.degraded_detail} />
      <h2 className="font-mono text-base font-semibold">{d.design_key}</h2>
      {d.summary && <p className="text-sm text-muted-foreground">{d.summary}</p>}

      <div>
        <SectionHeading as="h3">Option 一覧</SectionHeading>
        {d.options.length === 0 ? (
          <EmptyNote>Option はまだありません。</EmptyNote>
        ) : (
          <ul className="mt-2 space-y-1" data-testid="ux-solution-design-options">
            {d.options
              .filter((o) => o.superseded_by_id === null)
              .map((o) => (
                <li key={o.id} className="rounded border p-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono">{o.option_key}</span>
                    <StateBadge
                      label={OPTION_STATUS_LABEL[o.option_status]}
                      tone={o.option_status === "adopted" ? "success" : o.option_status === "rejected" ? "destructive" : "outline"}
                    />
                  </div>
                  {o.title && <div className="mt-1 text-xs text-muted-foreground">{o.title}</div>}
                </li>
              ))}
          </ul>
        )}
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => setOptionFormOpen((v) => !v)}>
          {optionFormOpen ? "Option の追加を閉じる" : "Option を追加する"}
        </Button>
        {optionFormOpen && (
          <AddOptionForm designKey={designKey} nextOrder={d.options.length + 1} onDone={() => setOptionFormOpen(false)} />
        )}
      </div>

      <div>
        <SectionHeading as="h3">紐づく Requirement</SectionHeading>
        {d.requirement_links.filter((l) => l.superseded_by_id === null).length === 0 ? (
          <EmptyNote>まだ Requirement への紐づけがありません。</EmptyNote>
        ) : (
          <ul className="mt-2 space-y-1 text-xs">
            {d.requirement_links
              .filter((l) => l.superseded_by_id === null)
              .map((l) => (
                <li key={l.id} className="rounded border p-2">
                  <span className="font-mono">{l.requirement_key ?? l.requirement_id}</span>
                  <StateBadge
                    label={LINK_STATE_LABEL[l.link_state]}
                    tone={l.link_state === "current" ? "outline" : "warning"}
                  />
                </li>
              ))}
          </ul>
        )}
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => setReqLinkOpen((v) => !v)}>
          {reqLinkOpen ? "紐づけの追加を閉じる" : "Requirement に紐づける"}
        </Button>
        {reqLinkOpen && <AddRequirementLinkForm designKey={designKey} onDone={() => setReqLinkOpen(false)} />}
      </div>

      {optionKeys.length > 0 && (
        <div>
          <SectionHeading as="h3">実装対象への紐づけ(Option ごと)</SectionHeading>
          <Button variant="ghost" size="sm" onClick={() => setTargetLinkOpen((v) => !v)}>
            {targetLinkOpen ? "紐づけの追加を閉じる" : "実装対象に紐づける"}
          </Button>
          {targetLinkOpen && (
            <AddTargetLinkForm designKey={designKey} optionKeys={optionKeys} onDone={() => setTargetLinkOpen(false)} />
          )}
        </div>
      )}

      {optionKeys.length > 0 && <OptionDecisionControls designKey={designKey} optionKeys={optionKeys} />}

      <ChangeOriginsSection designKey={designKey} />

      <HandoffSection designKey={designKey} />
    </div>
  );
}

export function SolutionDesignPanel({
  selectedKey,
  onSelect,
}: {
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[360px_1fr] lg:items-start">
      <SolutionDesignList selectedKey={selectedKey} onSelect={onSelect} />
      <div>
        {selectedKey === null ? (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground" data-testid="ux-solution-design-none-selected">
              左の一覧から Solution Design を選択してください。
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-6">
              <SolutionDesignDetail designKey={selectedKey} />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
