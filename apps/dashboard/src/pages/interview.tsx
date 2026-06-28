import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  CheckCircle, FileCode, GitPullRequest, MessageSquareText, Pencil,
  Play, Send, Sparkles, XCircle,
} from "lucide-react";
import {
  useApproveInterviewProposal,
  useCreateInterviewSession,
  useEditInterviewProposal,
  useInterviewApprovedSet,
  useInterviewContextPack,
  useInterviewDialogueTurn,
  useInterviewSession,
  useInterviewSessions,
  useLatestSnapshot,
  useMaterializeInterview,
  useRejectInterviewProposal,
} from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatTimestamp } from "@/lib/utils";
import type {
  InterviewMaterializeOut,
  InterviewProposalMetadataBlock,
  InterviewProposalOut,
  InterviewProposalProbePlan,
  ProbeRecommendedMode,
  ProbeReplayability,
  ProbeSideEffectRisk,
  SourceMetadataElementType,
  SourceMetadataOperationKind,
  SourceMetadataStateEffect,
} from "@/api/types";

const ELEMENT_TYPES: Array<"" | SourceMetadataElementType> = [
  "", "system", "core", "capability", "element", "supporting", "boundary",
];
const OPERATION_KINDS: Array<"" | SourceMetadataOperationKind> = [
  "", "analysis", "read", "write", "mutation", "io", "orchestration", "validation", "other",
];
const PROBE_MODES: ProbeRecommendedMode[] = ["trace", "shadow"];
const RISK_LEVELS: ProbeSideEffectRisk[] = ["none", "low", "medium", "high"];
const REPLAYABILITY: ProbeReplayability[] = ["safe", "caution", "unsafe"];

function provenanceVariant(value: string) {
  if (value === "manual") return "success" as const;
  if (value === "reasoning_llm") return "secondary" as const;
  return "outline" as const;
}

function approvalVariant(value: string) {
  if (value === "approved" || value === "edited") return "success" as const;
  if (value === "rejected") return "destructive" as const;
  return "secondary" as const;
}

function csv(items: string[]) {
  return items.join(", ");
}

function splitCsv(value: string) {
  return value.split(",").map(v => v.trim()).filter(Boolean);
}

function openDiff(diff: string, sessionId: number) {
  const blob = new Blob([diff], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  window.open(url, `probe-interview-${sessionId}-diff`);
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

type EditForm = {
  metadata: {
    role: string;
    capability: string;
    system_purpose: string;
    probe_value: string;
    element_type: "" | SourceMetadataElementType;
    operation_kind: "" | SourceMetadataOperationKind;
    consumers: string;
    state_effects: string;
  };
  probe_plan: InterviewProposalProbePlan;
};

function formFromProposal(proposal: InterviewProposalOut): EditForm {
  return {
    metadata: {
      role: proposal.metadata.role ?? "",
      capability: proposal.metadata.capability ?? "",
      system_purpose: proposal.metadata.system_purpose ?? "",
      probe_value: proposal.metadata.probe_value ?? "",
      element_type: proposal.metadata.element_type ?? "",
      operation_kind: proposal.metadata.operation_kind ?? "",
      consumers: csv(proposal.metadata.consumers),
      state_effects: csv(proposal.metadata.state_effects),
    },
    probe_plan: { ...proposal.probe_plan },
  };
}

function metadataFromForm(form: EditForm): InterviewProposalMetadataBlock {
  return {
    role: form.metadata.role.trim() || null,
    capability: form.metadata.capability.trim() || null,
    system_purpose: form.metadata.system_purpose.trim() || null,
    probe_value: form.metadata.probe_value.trim() || null,
    element_type: form.metadata.element_type || null,
    operation_kind: form.metadata.operation_kind || null,
    consumers: splitCsv(form.metadata.consumers),
    state_effects: splitCsv(form.metadata.state_effects) as SourceMetadataStateEffect[],
  };
}

function AuditBadge({ proposal }: { proposal: InterviewProposalOut }) {
  const run = proposal.intelligence_run;
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Badge variant={provenanceVariant(proposal.decision_method)}>
        {proposal.decision_method}
      </Badge>
      {proposal.is_mock && <Badge variant="warning">mock</Badge>}
      {run?.model && <Badge variant="outline">{run.provider ?? "llm"} / {run.model}</Badge>}
      {run?.prompt_version && <Badge variant="outline">{run.prompt_version}</Badge>}
    </div>
  );
}

function MetadataGrid({ metadata, probe }: {
  metadata: InterviewProposalMetadataBlock;
  probe: InterviewProposalProbePlan;
}) {
  const rows = [
    ["role", metadata.role],
    ["capability", metadata.capability],
    ["purpose", metadata.system_purpose],
    ["probe value", metadata.probe_value],
    ["element", metadata.element_type],
    ["operation", metadata.operation_kind],
    ["consumers", metadata.consumers.join(", ")],
    ["state", metadata.state_effects.join(", ")],
    ["feature", probe.feature_id],
    ["objective", probe.objective],
    ["mode", probe.recommended_mode],
    ["risk", probe.side_effect_risk],
    ["replay", probe.replayability],
  ];
  return (
    <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="uppercase text-[10px] text-muted-foreground">{label}</dt>
          <dd className="break-words">{value || "-"}</dd>
        </div>
      ))}
      {probe.reason && (
        <div className="md:col-span-2 min-w-0">
          <dt className="uppercase text-[10px] text-muted-foreground">reason</dt>
          <dd className="break-words">{probe.reason}</dd>
        </div>
      )}
    </dl>
  );
}

export default function InterviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionParam = Number(searchParams.get("session"));
  const selectedSessionId = Number.isFinite(sessionParam) && sessionParam > 0 ? sessionParam : null;
  const { user } = useAuth();
  const actor = user?.username ?? "dashboard";

  const { data: latestSnapshot, isLoading: snapshotLoading } = useLatestSnapshot();
  const { data: sessions, isLoading: sessionsLoading } = useInterviewSessions();
  const createSession = useCreateInterviewSession();
  const { data: session, isLoading: sessionLoading } = useInterviewSession(selectedSessionId);
  const { data: contextPack } = useInterviewContextPack(selectedSessionId);
  const { data: approvedSet } = useInterviewApprovedSet(selectedSessionId);
  const dialogueTurn = useInterviewDialogueTurn(selectedSessionId);
  const approve = useApproveInterviewProposal(selectedSessionId);
  const reject = useRejectInterviewProposal(selectedSessionId);
  const edit = useEditInterviewProposal(selectedSessionId);
  const materialize = useMaterializeInterview(selectedSessionId);

  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState<InterviewProposalOut | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [lastMaterialization, setLastMaterialization] = useState<InterviewMaterializeOut | null>(null);

  const sortedSessions = useMemo(() => sessions ?? [], [sessions]);
  const proposals = session?.proposals ?? [];
  const pendingCount = proposals.filter(p => p.approval_state === "proposed").length;
  const diff = lastMaterialization?.diff || session?.materialization_diff || "";

  useEffect(() => {
    if (!selectedSessionId && sortedSessions.length > 0) {
      setSearchParams({ session: String(sortedSessions[0].id) }, { replace: true });
    }
  }, [selectedSessionId, sortedSessions, setSearchParams]);

  const unclassifiedPreview = useMemo(
    () => contextPack?.symbols.filter(s => s.classification === "unclassified").slice(0, 6) ?? [],
    [contextPack],
  );

  const startSession = async () => {
    if (!latestSnapshot) {
      toast.error("Create a repository snapshot first");
      return;
    }
    try {
      const created = await createSession.mutateAsync({
        snapshot_id: latestSnapshot.id,
        title: `System interview ${latestSnapshot.commit_sha.slice(0, 8)}`,
        focus: "Author reviewed probe-agent metadata and probe proposals",
      });
      setSearchParams({ session: String(created.id) });
      toast.success("Interview started");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const sendTurn = async () => {
    const text = message.trim();
    if (!text || !selectedSessionId) return;
    try {
      const result = await dialogueTurn.mutateAsync({ user_message: text });
      setMessage("");
      if (result.error) toast.error(result.error);
      else toast.success(result.proposals.length ? `${result.proposals.length} proposal(s) generated` : "Interview turn saved");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const openEdit = (proposal: InterviewProposalOut) => {
    setEditing(proposal);
    setEditForm(formFromProposal(proposal));
  };

  const saveEdit = async () => {
    if (!editing || !editForm) return;
    try {
      await edit.mutateAsync({
        proposalId: editing.id,
        actor,
        metadata: metadataFromForm(editForm),
        probe_plan: editForm.probe_plan,
      });
      toast.success("Edited proposal approved");
      setEditing(null);
      setEditForm(null);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const triggerMaterialization = async () => {
    if (!selectedSessionId) return;
    try {
      const result = await materialize.mutateAsync();
      setLastMaterialization(result);
      toast.success(`Materialized ${result.items_materialized} item(s)`);
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <MessageSquareText className="h-6 w-6" /> System Interview
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            Snapshot-scoped review for LLM-authored metadata and probe proposals.
            Manual approval is recorded through the approval API before any diff is materialized.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            className="w-[240px]"
            value={selectedSessionId ? String(selectedSessionId) : ""}
            onChange={e => setSearchParams(e.target.value ? { session: e.target.value } : {})}
            disabled={!sortedSessions.length}
            aria-label="Interview session"
          >
            <option value="">No session selected</option>
            {sortedSessions.map(s => (
              <option key={s.id} value={s.id}>
                #{s.id} · snapshot {s.snapshot_id} · {s.status}
              </option>
            ))}
          </Select>
          <Button size="sm" onClick={startSession} disabled={createSession.isPending || snapshotLoading || !latestSnapshot}>
            <Sparkles className="h-4 w-4 mr-1" />
            {createSession.isPending ? "Starting..." : "Start interview"}
          </Button>
        </div>
      </div>

      {!latestSnapshot && !snapshotLoading && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No repository snapshot is available. Create one on the{" "}
            <Link className="underline" to="/repository">Repository</Link> page before starting an interview.
          </CardContent>
        </Card>
      )}

      {sessionsLoading || sessionLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : !selectedSessionId || !session ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Start an interview from the latest repository snapshot or continue an existing session.
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-[360px_1fr] gap-4">
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Session #{session.id}</CardTitle>
                  <CardDescription>
                    Snapshot {session.snapshot_id} · {formatTimestamp(session.updated_at)}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-md border p-2">
                      <div className="text-lg font-semibold">{proposals.length}</div>
                      <div className="text-xs text-muted-foreground">proposals</div>
                    </div>
                    <div className="rounded-md border p-2">
                      <div className="text-lg font-semibold">{approvedSet?.approved_count ?? 0}</div>
                      <div className="text-xs text-muted-foreground">approved</div>
                    </div>
                    <div className="rounded-md border p-2">
                      <div className="text-lg font-semibold">{pendingCount}</div>
                      <div className="text-xs text-muted-foreground">pending</div>
                    </div>
                  </div>
                  {contextPack && (
                    <div className="rounded-md border p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="font-medium">Context pack</span>
                        <Badge variant={contextPack.truncated ? "warning" : "secondary"}>
                          {contextPack.budget_used_chars.toLocaleString()} chars
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {contextPack.total_symbols} symbols · {contextPack.total_entrypoints} entrypoints ·{" "}
                        {contextPack.unclassified_count} unclassified
                      </div>
                      <div className="space-y-1">
                        {unclassifiedPreview.map(s => (
                          <div key={s.symbol_id} className="truncate text-xs font-mono">
                            {s.path}:{s.qualified_name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Conversation</CardTitle>
                  <CardDescription>Turns are grounded in the pinned snapshot context.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="space-y-2 max-h-[24rem] overflow-y-auto pr-1">
                    {session.messages.length === 0 ? (
                      <div className="text-sm text-muted-foreground">No turns yet.</div>
                    ) : session.messages.map(m => (
                      <div key={m.id} className="rounded-md border p-3 text-sm">
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <Badge variant={m.role === "assistant" ? "secondary" : "outline"}>{m.role}</Badge>
                          <span className="text-xs text-muted-foreground">{formatTimestamp(m.created_at)}</span>
                        </div>
                        <p className="whitespace-pre-wrap break-words">{m.content}</p>
                      </div>
                    ))}
                  </div>
                  <Textarea
                    rows={5}
                    value={message}
                    onChange={e => setMessage(e.target.value)}
                    placeholder="Ask for metadata and probe proposals for the unclassified entrypoints."
                  />
                  <Button size="sm" onClick={sendTurn} disabled={dialogueTurn.isPending || !message.trim()}>
                    <Send className="h-4 w-4 mr-1" />
                    {dialogueTurn.isPending ? "Sending..." : "Send turn"}
                  </Button>
                </CardContent>
              </Card>
            </div>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <CardTitle className="text-sm">Proposal Review</CardTitle>
                      <CardDescription>Approve, reject, or edit each combined metadata + probe item.</CardDescription>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={triggerMaterialization}
                      disabled={materialize.isPending || (approvedSet?.approved_count ?? 0) === 0}
                    >
                      <Play className="h-4 w-4 mr-1" />
                      {materialize.isPending ? "Materializing..." : "Materialize"}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {proposals.length === 0 ? (
                    <div className="text-sm text-muted-foreground">No proposals yet. Send an interview turn to generate review items.</div>
                  ) : proposals.map(proposal => (
                    <div key={proposal.id} className="rounded-md border p-4 space-y-3" data-testid="interview-proposal-card">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="font-mono text-sm truncate">{proposal.qualified_name}</div>
                          <div className="text-xs text-muted-foreground truncate">{proposal.path}</div>
                        </div>
                        <div className="flex flex-col items-end gap-1 shrink-0">
                          <Badge variant={approvalVariant(proposal.approval_state)}>{proposal.approval_state}</Badge>
                          <AuditBadge proposal={proposal} />
                        </div>
                      </div>
                      <MetadataGrid metadata={proposal.metadata} probe={proposal.probe_plan} />
                      <div className="flex items-center gap-2 justify-end">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => openEdit(proposal)}
                          disabled={proposal.approval_state !== "proposed" || edit.isPending}
                        >
                          <Pencil className="h-4 w-4 mr-1" />
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => approve.mutateAsync({ proposalId: proposal.id, actor }).then(() => toast.success("Proposal approved")).catch(e => toast.error(String(e)))}
                          disabled={proposal.approval_state !== "proposed" || approve.isPending}
                        >
                          <CheckCircle className="h-4 w-4 mr-1 text-emerald-600" />
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => reject.mutateAsync({ proposalId: proposal.id, actor }).then(() => toast.success("Proposal rejected")).catch(e => toast.error(String(e)))}
                          disabled={proposal.approval_state !== "proposed" || reject.isPending}
                        >
                          <XCircle className="h-4 w-4 mr-1 text-red-500" />
                          Reject
                        </Button>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <CardTitle className="text-sm">Reviewable Diff</CardTitle>
                      <CardDescription>Materialization stops at a diff artifact for developer review.</CardDescription>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => openDiff(diff, session.id)} disabled={!diff}>
                      <GitPullRequest className="h-4 w-4 mr-1" />
                      Open diff
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {lastMaterialization?.skipped?.length ? (
                    <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-xs text-amber-900 dark:text-amber-100">
                      {lastMaterialization.skipped.join("; ")}
                    </div>
                  ) : null}
                  {diff ? (
                    <pre className="max-h-[28rem] overflow-auto rounded-md border bg-muted p-3 text-xs whitespace-pre-wrap">
                      {diff}
                    </pre>
                  ) : (
                    <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
                      Approve at least one proposal, then materialize to generate a single combined diff.
                    </div>
                  )}
                  {session.materialization_ref && (
                    <a className="inline-flex items-center text-sm underline" href={session.materialization_ref} target="_blank" rel="noreferrer">
                      <FileCode className="h-4 w-4 mr-1" />
                      Open materialization reference
                    </a>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>

          <Dialog open={!!editing && !!editForm} onOpenChange={(open) => { if (!open) { setEditing(null); setEditForm(null); } }}>
            {editing && editForm && (
              <>
                <DialogHeader>
                  <DialogTitle>Edit Proposal</DialogTitle>
                </DialogHeader>
                <div className="space-y-4">
                  <div className="text-xs font-mono text-muted-foreground">{editing.path}:{editing.qualified_name}</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <Field label="Role" value={editForm.metadata.role} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, role: v } })} />
                    <Field label="Capability" value={editForm.metadata.capability} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, capability: v } })} />
                    <Field label="System purpose" value={editForm.metadata.system_purpose} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, system_purpose: v } })} />
                    <Field label="Probe value" value={editForm.metadata.probe_value} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, probe_value: v } })} />
                    <div>
                      <Label>Element type</Label>
                      <Select value={editForm.metadata.element_type} onChange={e => setEditForm({ ...editForm, metadata: { ...editForm.metadata, element_type: e.target.value as "" | SourceMetadataElementType } })}>
                        {ELEMENT_TYPES.map(v => <option key={v || "none"} value={v}>{v || "unset"}</option>)}
                      </Select>
                    </div>
                    <div>
                      <Label>Operation kind</Label>
                      <Select value={editForm.metadata.operation_kind} onChange={e => setEditForm({ ...editForm, metadata: { ...editForm.metadata, operation_kind: e.target.value as "" | SourceMetadataOperationKind } })}>
                        {OPERATION_KINDS.map(v => <option key={v || "none"} value={v}>{v || "unset"}</option>)}
                      </Select>
                    </div>
                    <Field label="Consumers" value={editForm.metadata.consumers} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, consumers: v } })} />
                    <Field label="State effects" value={editForm.metadata.state_effects} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, state_effects: v } })} />
                    <Field label="Feature id" value={editForm.probe_plan.feature_id} onChange={v => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, feature_id: v } })} />
                    <Field label="Objective" value={editForm.probe_plan.objective} onChange={v => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, objective: v } })} />
                    <div>
                      <Label>Mode</Label>
                      <Select value={editForm.probe_plan.recommended_mode} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, recommended_mode: e.target.value as ProbeRecommendedMode } })}>
                        {PROBE_MODES.map(v => <option key={v} value={v}>{v}</option>)}
                      </Select>
                    </div>
                    <div>
                      <Label>Risk</Label>
                      <Select value={editForm.probe_plan.side_effect_risk} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, side_effect_risk: e.target.value as ProbeSideEffectRisk } })}>
                        {RISK_LEVELS.map(v => <option key={v} value={v}>{v}</option>)}
                      </Select>
                    </div>
                    <div>
                      <Label>Replayability</Label>
                      <Select value={editForm.probe_plan.replayability} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, replayability: e.target.value as ProbeReplayability } })}>
                        {REPLAYABILITY.map(v => <option key={v} value={v}>{v}</option>)}
                      </Select>
                    </div>
                  </div>
                  <div>
                    <Label>Reason</Label>
                    <Textarea rows={4} value={editForm.probe_plan.reason} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, reason: e.target.value } })} />
                  </div>
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={() => { setEditing(null); setEditForm(null); }}>Cancel</Button>
                    <Button onClick={saveEdit} disabled={edit.isPending}>
                      {edit.isPending ? "Saving..." : "Save manual edit"}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </Dialog>
        </>
      )}
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <div>
      <Label>{label}</Label>
      <Input value={value} onChange={e => onChange(e.target.value)} />
    </div>
  );
}
