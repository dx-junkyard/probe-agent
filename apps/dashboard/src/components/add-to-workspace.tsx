import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useWorkspaces, useCreateWorkspace } from "@/api/hooks";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { MessageSquarePlus } from "lucide-react";
import type { WorkspaceContextItemType, WorkspaceContextItemOut } from "@/api/types";

export function AddToWorkspaceButton({ itemType, itemId, label }: {
  itemType: WorkspaceContextItemType;
  itemId: string;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <MessageSquarePlus className="h-4 w-4 mr-1" />
        Add to Workspace
      </Button>
      <AddToWorkspaceDialog open={open} onOpenChange={setOpen} itemType={itemType} itemId={itemId} label={label} />
    </>
  );
}

function AddToWorkspaceDialog({ open, onOpenChange, itemType, itemId, label }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  itemType: WorkspaceContextItemType;
  itemId: string;
  label?: string;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: workspaces } = useWorkspaces();
  const createWorkspace = useCreateWorkspace();
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [selectedId, setSelectedId] = useState<string>("");
  const [newTitle, setNewTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reset = () => { setMode("existing"); setSelectedId(""); setNewTitle(""); };

  const handleAdd = async () => {
    setSubmitting(true);
    try {
      let workspaceId = Number(selectedId);
      if (mode === "new") {
        const created = await createWorkspace.mutateAsync({ title: newTitle.trim() });
        workspaceId = created.id;
      }
      if (!workspaceId) {
        toast.error("Select or create a workspace first");
        return;
      }
      await api.post<WorkspaceContextItemOut>(`/workspaces/${workspaceId}/context`, { item_type: itemType, item_id: itemId, label });
      qc.invalidateQueries({ queryKey: ["workspace"] });
      toast.success("Added to workspace");
      onOpenChange(false);
      reset();
      navigate(`/workspaces?open=${workspaceId}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = mode === "existing" ? !!selectedId : !!newTitle.trim();

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogHeader>
        <DialogTitle>Add to Decision Workspace</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Pin <span className="font-mono">{itemId}</span> ({itemType}) as context in a workspace.
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant={mode === "existing" ? "default" : "outline"} onClick={() => setMode("existing")}>
            Existing
          </Button>
          <Button size="sm" variant={mode === "new" ? "default" : "outline"} onClick={() => setMode("new")}>
            New Workspace
          </Button>
        </div>
        {mode === "existing" ? (
          <div className="space-y-2">
            <Label>Workspace</Label>
            <Select value={selectedId} onChange={e => setSelectedId(e.target.value)}>
              <option value="">Select a workspace...</option>
              {workspaces?.map(w => <option key={w.id} value={w.id}>{w.title}</option>)}
            </Select>
            {!workspaces?.length && <p className="text-xs text-muted-foreground">No workspaces yet. Create one instead.</p>}
          </div>
        ) : (
          <div className="space-y-2">
            <Label>Title</Label>
            <Input value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder="Improve summarizer quality" />
          </div>
        )}
        <Button className="w-full" disabled={!canSubmit || submitting} onClick={handleAdd}>
          {submitting ? "Adding..." : "Add"}
        </Button>
      </div>
    </Dialog>
  );
}
