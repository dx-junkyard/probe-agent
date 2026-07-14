import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Play, ListPlus, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useReplaySets, useCreateReplaySet } from "@/api/hooks";
import type { Replayability } from "@/api/types";

// Reusable trace row actions for the Simulation Workbench (Issue #242 Phase D
// / #246): a replayability badge (with a reason-code tooltip) plus the three
// one-click actions that get a trace into the Workbench or Experiments flow.
// Reused by Components' Traces tab, Trace Lineage, and analyzer example rows
// wherever a trace_id + its owning component_id are both available.

const REPLAYABILITY_VARIANT: Record<string, "success" | "warning" | "secondary"> = {
  replayable: "success",
  partial: "warning",
  unreplayable: "secondary",
};

const REPLAYABILITY_LABEL: Record<string, string> = {
  replayable: "replayable",
  partial: "partial",
  unreplayable: "unreplayable",
};

export function ReplayabilityBadge({
  replayability,
  reasons,
}: {
  replayability?: Replayability | null;
  reasons?: string[] | null;
}) {
  if (!replayability) {
    return (
      <Badge
        variant="outline"
        className="text-[10px]"
        title="No structured input capture was recorded for this trace (the component has not opted into replay_capture, or this trace predates it)."
      >
        not captured
      </Badge>
    );
  }
  const title = reasons && reasons.length > 0 ? `Reasons: ${reasons.join(", ")}` : undefined;
  return (
    <Badge
      variant={REPLAYABILITY_VARIANT[replayability] ?? "outline"}
      className="text-[10px]"
      title={title}
    >
      {REPLAYABILITY_LABEL[replayability] ?? replayability}
    </Badge>
  );
}

export function ReplayRowActions({
  componentId,
  traceId,
}: {
  componentId: string;
  traceId: string;
}) {
  const navigate = useNavigate();
  const [dialogMode, setDialogMode] = useState<"replay" | "add" | null>(null);

  return (
    <div className="flex items-center gap-1 flex-wrap">
      <Button
        size="sm"
        variant="ghost"
        className="h-7 px-2 text-xs"
        title="Add this trace to a Replay Set and open the Simulation Workbench"
        onClick={() => setDialogMode("replay")}
      >
        <Play className="h-3 w-3 mr-1" /> Replay
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-7 px-2 text-xs"
        title="Add this trace to a new or existing Replay Set"
        onClick={() => setDialogMode("add")}
      >
        <ListPlus className="h-3 w-3 mr-1" /> Add to Replay Set
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-7 px-2 text-xs"
        title="Create an Experiment from this trace"
        onClick={() =>
          navigate(
            `/experiments?from_trace=${encodeURIComponent(traceId)}&from_component=${encodeURIComponent(componentId)}`,
          )
        }
      >
        <FlaskConical className="h-3 w-3 mr-1" /> Create Experiment
      </Button>
      {dialogMode && (
        <ReplaySetDialog
          mode={dialogMode}
          componentId={componentId}
          traceId={traceId}
          onOpenChange={(open) => {
            if (!open) setDialogMode(null);
          }}
        />
      )}
    </div>
  );
}

function ReplaySetDialog({
  mode,
  componentId,
  traceId,
  onOpenChange,
}: {
  mode: "replay" | "add";
  componentId: string;
  traceId: string;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const { data: sets } = useReplaySets(componentId);
  const createSet = useCreateReplaySet();
  const [pickMode, setPickMode] = useState<"new" | "existing">("new");
  const [selectedId, setSelectedId] = useState("");
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      let traceIds = [traceId];
      let name = newName.trim();
      if (pickMode === "existing" && selectedId) {
        const existing = sets?.find((s) => String(s.id) === selectedId);
        if (existing) {
          traceIds = Array.from(new Set([...existing.trace_ids, traceId]));
          name = existing.name;
        }
      }
      if (!name) name = `Replay set: ${componentId}`;
      const created = await createSet.mutateAsync({
        component_id: componentId,
        name,
        trace_ids: traceIds,
      });
      toast.success(mode === "replay" ? "Replay set ready" : "Added to Replay Set");
      onOpenChange(false);
      if (mode === "replay") {
        navigate(`/simulation-workbench?replay_set_id=${created.id}`);
      }
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>{mode === "replay" ? "Replay this trace" : "Add to Replay Set"}</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Trace <span className="font-mono">{traceId}</span> ({componentId})
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant={pickMode === "new" ? "default" : "outline"}
            onClick={() => setPickMode("new")}
          >
            New Replay Set
          </Button>
          <Button
            size="sm"
            variant={pickMode === "existing" ? "default" : "outline"}
            onClick={() => setPickMode("existing")}
            disabled={!sets?.length}
          >
            Existing Set
          </Button>
        </div>
        {pickMode === "existing" ? (
          <div className="space-y-2">
            <Label>Replay Set</Label>
            <Select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
              <option value="">Select a replay set...</option>
              {sets?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || `Set #${s.id}`} ({s.trace_ids.length} traces)
                </option>
              ))}
            </Select>
          </div>
        ) : (
          <div className="space-y-2">
            <Label>Name</Label>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={`Replay set: ${componentId}`}
            />
          </div>
        )}
        <Button
          className="w-full"
          disabled={submitting || (pickMode === "existing" && !selectedId)}
          onClick={handleSubmit}
        >
          {submitting ? "Saving..." : mode === "replay" ? "Replay" : "Add"}
        </Button>
      </div>
    </Dialog>
  );
}
