import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  useWorkspaces, useWorkspace, useCreateWorkspace, useWorkspaceContextPack,
  useAddWorkspaceContextItem, useDeleteWorkspaceContextItem,
  useCreateWorkspaceAgentTurn, useAcceptWorkspaceProposal, useRejectWorkspaceProposal,
  useDeferWorkspaceProposal, useCreateWorkspaceProposalDraft,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { formatTimestamp, cn } from "@/lib/utils";
import { Plus, Send, Trash2, CheckCircle, XCircle, ExternalLink, Clock3 } from "lucide-react";
import type { WorkspaceContextItemType, WorkspaceProposalOut } from "@/api/types";

const ITEM_TYPE_ROUTE: Record<string, string> = {
  feature: "/feature-map",
  component: "/components",
  trace: "/components",
  experiment: "/experiments",
  probe_plan: "/probe-planner",
};

const ITEM_TYPES: WorkspaceContextItemType[] = ["feature", "component", "trace", "experiment", "probe_plan"];

export default function WorkspacesPage() {
  const [params] = useSearchParams();
  const queryOpen = params.get("open");
  const { data: workspaces, isLoading: workspacesLoading } = useWorkspaces();
  const [selectedId, setSelectedId] = useState<number | null>(queryOpen ? Number(queryOpen) : null);
  const [showCreate, setShowCreate] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Decision Workspace</h1>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-1" />
          New Workspace
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr_320px]">
        <WorkspaceListPane
          workspaces={workspaces}
          isLoading={workspacesLoading}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <ConversationPane workspaceId={selectedId} />
        <ContextProposalsPane workspaceId={selectedId} />
      </div>

      <CreateWorkspaceDialog open={showCreate} onOpenChange={setShowCreate} onCreated={setSelectedId} />
    </div>
  );
}

function CreateWorkspaceDialog({ open, onOpenChange, onCreated }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (id: number) => void;
}) {
  const createWorkspace = useCreateWorkspace();
  const [title, setTitle] = useState("");
  const [focus, setFocus] = useState("");

  const reset = () => { setTitle(""); setFocus(""); };

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogHeader>
        <DialogTitle>New Decision Workspace</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Title</Label>
          <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="Improve summarizer quality" />
        </div>
        <div className="space-y-2">
          <Label>Focus</Label>
          <Textarea value={focus} onChange={e => setFocus(e.target.value)} placeholder="What decision are you trying to make?" rows={2} />
        </div>
        <Button
          className="w-full"
          disabled={!title.trim() || createWorkspace.isPending}
          onClick={() => {
            createWorkspace.mutateAsync({ title: title.trim(), focus: focus.trim() || undefined })
              .then(w => { toast.success("Workspace created"); onCreated(w.id); onOpenChange(false); reset(); })
              .catch(e => toast.error(String(e)));
          }}
        >
          {createWorkspace.isPending ? "Creating..." : "Create Workspace"}
        </Button>
      </div>
    </Dialog>
  );
}

function WorkspaceListPane({ workspaces, isLoading, selectedId, onSelect }: {
  workspaces: { id: number; title: string; status: string; focus: string; updated_at: number }[] | undefined;
  isLoading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle className="text-sm">Workspaces</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {isLoading ? (
          <div className="space-y-2">{[1, 2].map(i => <Skeleton key={i} className="h-16 w-full" />)}</div>
        ) : !workspaces?.length ? (
          <p className="text-sm text-muted-foreground">No workspaces yet.</p>
        ) : (
          workspaces.map(w => (
            <button
              key={w.id}
              onClick={() => onSelect(w.id)}
              className={cn(
                "w-full rounded-lg border p-3 text-left text-sm transition-colors cursor-pointer",
                selectedId === w.id ? "border-primary bg-secondary" : "hover:bg-secondary/50",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium truncate">{w.title}</span>
                <Badge variant={w.status === "active" ? "success" : "secondary"}>{w.status}</Badge>
              </div>
              {w.focus && <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{w.focus}</p>}
              <p className="mt-1 text-xs text-muted-foreground">{formatTimestamp(w.updated_at)}</p>
            </button>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function ConversationPane({ workspaceId }: { workspaceId: number | null }) {
  const { data: workspace, isLoading, error } = useWorkspace(workspaceId);
  const sendTurn = useCreateWorkspaceAgentTurn(workspaceId ?? -1);
  const [message, setMessage] = useState("");
  const [turnError, setTurnError] = useState<string | null>(null);

  if (!workspaceId) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Select or create a workspace to start a conversation.
        </CardContent>
      </Card>
    );
  }

  if (isLoading) {
    return <div className="space-y-4">{[1, 2].map(i => <Skeleton key={i} className="h-24 w-full" />)}</div>;
  }

  if (error || !workspace) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-destructive">
          Could not load this workspace. It may belong to a different system.
        </CardContent>
      </Card>
    );
  }

  const handleSend = () => {
    if (!message.trim() || sendTurn.isPending) return;
    setTurnError(null);
    sendTurn.mutateAsync({ message: message.trim(), context_refs: [] })
      .then(result => {
        setMessage("");
        if (result.error) setTurnError(result.error);
      })
      .catch(e => toast.error(String(e)));
  };

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle className="text-sm">{workspace.title}</CardTitle>
        {workspace.focus && <CardDescription>{workspace.focus}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="space-y-3 max-h-[55vh] overflow-y-auto">
          {workspace.messages.length === 0 ? (
            <p className="text-sm text-muted-foreground">No messages yet. Ask a question to start the dialogue.</p>
          ) : (
            workspace.messages.map(m => <MessageBubble key={m.id} message={m} />)
          )}
        </div>

        {turnError && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-200">
            The assistant could not produce a valid structured response: {turnError}
          </div>
        )}

        <div className="space-y-2">
          <Textarea
            value={message}
            onChange={e => setMessage(e.target.value)}
            placeholder="Ask about this theme, grounded only in the pinned context..."
            rows={3}
            disabled={sendTurn.isPending}
          />
          <Button onClick={handleSend} disabled={!message.trim() || sendTurn.isPending} className="w-full">
            <Send className="h-4 w-4 mr-1" />
            {sendTurn.isPending ? "Sending..." : "Send"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function MessageBubble({ message }: { message: { role: string; content: string; context_metadata: Record<string, unknown>; created_at: number } }) {
  const isAssistant = message.role === "assistant";
  const groundedFindings = (message.context_metadata.grounded_findings as Array<{ claim: string; source_type: string; source_id: string }> | undefined) ?? [];
  const assumptions = (message.context_metadata.assumptions as string[] | undefined) ?? [];
  const missingInformation = (message.context_metadata.missing_information as string[] | undefined) ?? [];
  const nextQuestions = (message.context_metadata.next_questions as string[] | undefined) ?? [];
  const provider = message.context_metadata.provider as string | undefined;
  const model = message.context_metadata.model as string | undefined;

  return (
    <div className={cn("rounded-lg border p-3 text-sm", isAssistant ? "bg-secondary/30" : "bg-background")}>
      <div className="flex items-center justify-between gap-2 mb-1">
        <Badge variant={isAssistant ? "default" : "outline"}>{message.role}</Badge>
        <span className="text-xs text-muted-foreground">{formatTimestamp(message.created_at)}</span>
      </div>
      <p className="whitespace-pre-wrap">{message.content}</p>

      {isAssistant && groundedFindings.length > 0 && (
        <div className="mt-2 space-y-1">
          {groundedFindings.map((f, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <Badge variant="success" className="shrink-0">grounded</Badge>
              <span>{f.claim} <span className="text-muted-foreground">({f.source_type}:{f.source_id})</span></span>
            </div>
          ))}
        </div>
      )}
      {isAssistant && assumptions.length > 0 && (
        <div className="mt-2 space-y-1">
          {assumptions.map((a, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <Badge variant="warning" className="shrink-0">assumption</Badge>
              <span>{a}</span>
            </div>
          ))}
        </div>
      )}
      {isAssistant && missingInformation.length > 0 && (
        <div className="mt-2 space-y-1">
          {missingInformation.map((m, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <Badge variant="secondary" className="shrink-0">missing</Badge>
              <span>{m}</span>
            </div>
          ))}
        </div>
      )}
      {isAssistant && nextQuestions.length > 0 && (
        <div className="mt-2">
          <p className="text-xs font-medium text-muted-foreground">Next questions</p>
          <ul className="list-disc pl-4 text-xs">
            {nextQuestions.map((q, i) => <li key={i}>{q}</li>)}
          </ul>
        </div>
      )}
      {isAssistant && (provider || model) && (
        <p className="mt-2 text-[10px] text-muted-foreground">decision_method: reasoning_llm · {provider}/{model}</p>
      )}
    </div>
  );
}

function ContextProposalsPane({ workspaceId }: { workspaceId: number | null }) {
  const navigate = useNavigate();
  const { data: workspace } = useWorkspace(workspaceId);
  const { data: contextPack } = useWorkspaceContextPack(workspaceId);
  const addContextItem = useAddWorkspaceContextItem(workspaceId ?? -1);
  const deleteContextItem = useDeleteWorkspaceContextItem(workspaceId ?? -1);
  const acceptProposal = useAcceptWorkspaceProposal(workspaceId ?? -1);
  const rejectProposal = useRejectWorkspaceProposal(workspaceId ?? -1);
  const deferProposal = useDeferWorkspaceProposal(workspaceId ?? -1);
  const createDraft = useCreateWorkspaceProposalDraft(workspaceId ?? -1);

  const [showAddContext, setShowAddContext] = useState(false);
  const [newItemType, setNewItemType] = useState<WorkspaceContextItemType>("component");
  const [newItemId, setNewItemId] = useState("");
  const [newItemLabel, setNewItemLabel] = useState("");
  const [reasons, setReasons] = useState<Record<number, string>>({});

  if (!workspaceId || !workspace) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Context, proposals, and decisions appear here once a workspace is selected.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-sm">Context</CardTitle>
          <Button size="sm" variant="ghost" onClick={() => setShowAddContext(true)}>
            <Plus className="h-3 w-3 mr-1" /> Add
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {workspace.context_items.length === 0 ? (
            <p className="text-xs text-muted-foreground">No context pinned yet.</p>
          ) : (
            workspace.context_items.map(item => (
              <div key={item.id} className="flex items-center justify-between gap-2 rounded-md border p-2 text-xs">
                <div className="min-w-0">
                  <div className="flex items-center gap-1">
                    <Badge variant="outline">{item.item_type}</Badge>
                    <span className="font-mono truncate">{item.item_id}</span>
                  </div>
                  {item.label && <p className="text-muted-foreground truncate">{item.label}</p>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {ITEM_TYPE_ROUTE[item.item_type] && (
                    <Link to={ITEM_TYPE_ROUTE[item.item_type]} title="Open source screen">
                      <Button variant="ghost" size="icon" className="h-6 w-6">
                        <ExternalLink className="h-3 w-3" />
                      </Button>
                    </Link>
                  )}
                  <Button
                    variant="ghost" size="icon" className="h-6 w-6"
                    onClick={() => deleteContextItem.mutateAsync(item.id).catch(e => toast.error(String(e)))}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            ))
          )}
          {contextPack?.missing_information && contextPack.missing_information.length > 0 && (
            <div className="mt-2 space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Missing information</p>
              {contextPack.missing_information.map((m, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <Badge variant="secondary" className="shrink-0">missing</Badge>
                  <span>{m}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Proposals & Decisions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {workspace.proposals.length === 0 ? (
            <p className="text-xs text-muted-foreground">No proposals yet.</p>
          ) : (
            workspace.proposals.map(p => (
              <ProposalCard
                key={p.id}
                proposal={p}
                reason={reasons[p.id] ?? ""}
                onReasonChange={(v) => setReasons(prev => ({ ...prev, [p.id]: v }))}
                onAccept={() => {
                  if (!window.confirm(`Accept proposal "${p.title || `#${p.id}`}" with the recorded reason?`)) return;
                  acceptProposal.mutateAsync({ proposalId: p.id, reason: reasons[p.id] })
                    .then(() => toast.success("Proposal accepted"))
                    .catch(e => toast.error(String(e)));
                }}
                onReject={() => rejectProposal.mutateAsync({ proposalId: p.id, reason: reasons[p.id] }).then(() => toast.success("Proposal rejected")).catch(e => toast.error(String(e)))}
                onDefer={() => deferProposal.mutateAsync({ proposalId: p.id, reason: reasons[p.id] }).then(() => toast.success("Proposal deferred")).catch(e => toast.error(String(e)))}
                onCreateDraft={() => {
                  const likelyMissing = p.proposal_type === "experiment_draft"
                    ? [p.body.snapshot_id ? null : "snapshot_id", "patch_text"].filter(Boolean).join(", ")
                    : "repository/feature prerequisites (validated by the server)";
                  const preview = JSON.stringify(p.body, null, 2);
                  if (!window.confirm(
                    `Create an editable ${p.proposal_type}?\n\nPrefill:\n${preview}\n\nPotential missing fields: ${likelyMissing}`,
                  )) return;
                  createDraft.mutateAsync(p.id).then(draft => {
                    const target = draft.target_screen === "probe_planner" ? "/probe-planner" : "/experiments";
                    navigate(`${target}?draft=${draft.id}&workspace=${workspaceId}`);
                  }).catch(e => toast.error(String(e)));
                }}
                isPending={acceptProposal.isPending || rejectProposal.isPending || deferProposal.isPending || createDraft.isPending}
              />
            ))
          )}
        </CardContent>
      </Card>

      <Dialog open={showAddContext} onOpenChange={setShowAddContext}>
        <DialogHeader>
          <DialogTitle>Add Context Item</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Type</Label>
            <Select value={newItemType} onChange={e => setNewItemType(e.target.value as WorkspaceContextItemType)}>
              {ITEM_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Item ID</Label>
            <Input value={newItemId} onChange={e => setNewItemId(e.target.value)} placeholder="component_id / feature_id / experiment id / plan id" />
          </div>
          <div className="space-y-2">
            <Label>Label (optional)</Label>
            <Input value={newItemLabel} onChange={e => setNewItemLabel(e.target.value)} />
          </div>
          <Button
            className="w-full"
            disabled={!newItemId.trim() || addContextItem.isPending}
            onClick={() => {
              addContextItem.mutateAsync({ item_type: newItemType, item_id: newItemId.trim(), label: newItemLabel.trim() || undefined })
                .then(() => { toast.success("Context item added"); setShowAddContext(false); setNewItemId(""); setNewItemLabel(""); })
                .catch(e => toast.error(String(e)));
            }}
          >
            {addContextItem.isPending ? "Adding..." : "Add"}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function ProposalCard({ proposal, reason, onReasonChange, onAccept, onReject, onDefer, onCreateDraft, isPending }: {
  proposal: WorkspaceProposalOut;
  reason: string;
  onReasonChange: (v: string) => void;
  onAccept: () => void;
  onReject: () => void;
  onDefer: () => void;
  onCreateDraft: () => void;
  isPending: boolean;
}) {
  const decided = proposal.status !== "proposed";
  return (
    <div className="rounded-lg border p-3 space-y-2 text-xs">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant="outline">{proposal.proposal_type}</Badge>
          <span className="font-medium">{proposal.title || "Untitled proposal"}</span>
        </div>
        <Badge variant={proposal.status === "accepted" ? "success" : proposal.status === "rejected" ? "destructive" : "secondary"}>
          {proposal.status}
        </Badge>
      </div>
      <div className="space-y-1">
        {Object.entries(proposal.body).map(([k, v]) => (
          <div key={k}><span className="text-muted-foreground">{k}:</span> {Array.isArray(v) ? v.join(", ") : String(v)}</div>
        ))}
      </div>
      {!decided ? (
        <div className="space-y-2">
          <Textarea
            value={reason}
            onChange={e => onReasonChange(e.target.value)}
            placeholder="Reason for this decision..."
            rows={2}
          />
          <div className="flex gap-2">
            <Button size="sm" variant="outline" className="flex-1" onClick={onAccept} disabled={isPending}>
              <CheckCircle className="h-3 w-3 mr-1 text-emerald-600" /> Accept
            </Button>
            <Button size="sm" variant="outline" className="flex-1" onClick={onReject} disabled={isPending}>
              <XCircle className="h-3 w-3 mr-1 text-red-500" /> Reject
            </Button>
            <Button size="sm" variant="outline" className="flex-1" onClick={onDefer} disabled={isPending}>
              <Clock3 className="h-3 w-3 mr-1" /> Defer
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {proposal.decisions.length > 0 && (
            <p className="text-muted-foreground">
              {proposal.decisions[proposal.decisions.length - 1].decision} — {proposal.decisions[proposal.decisions.length - 1].reason || "(no reason given)"}
            </p>
          )}
          {proposal.status === "accepted" && (
            <Button size="sm" className="w-full" onClick={onCreateDraft} disabled={isPending}>
              {proposal.proposal_type === "probe_plan_draft"
                ? "Review in Probe Planner"
                : "Create Experiment draft"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
