import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Play, ListPlus, FlaskConical, Bot, MoreHorizontal } from "lucide-react";
import { formatTimestamp } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useReplaySets, useCreateReplaySet } from "@/api/hooks";
import type { Replayability } from "@/api/types";

// Reusable trace row actions for the Simulation Workbench (Issue #242 Phase D
// / #246): a replayability badge (with a reason-code tooltip) plus the actions
// that get a trace into Candidate Studio (primary), the Workbench, or
// Experiments (secondary, under 「その他の操作」).
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
  const moreRef = useRef<HTMLDetailsElement>(null);
  const closeMore = () => {
    if (moreRef.current) moreRef.current.open = false;
  };

  // UX audit 2026-11-17 C13: four equal-weight actions made the row read as
  // four products. The one route that carries the developer from a recorded
  // input to an evaluated candidate is Candidate Studio, so it is the single
  // primary action; the other three stay one click away under 「その他の操作」
  // (none removed). 「Experimentを作成」 opens the create form with no patch --
  // the title says so rather than implying the trace is enough.
  return (
    <div className="flex items-center gap-1 flex-wrap">
      <Button
        size="sm"
        variant="outline"
        className="h-7 px-2 text-xs"
        title="このTraceの入力を起点にAI Candidate Studioセッションを開始する"
        onClick={() =>
          navigate(
            `/candidate-studio?component_id=${encodeURIComponent(componentId)}&trace_id=${encodeURIComponent(traceId)}`,
          )
        }
      >
        <Bot className="h-3 w-3 mr-1" /> この入力から改善する
      </Button>
      <details
        ref={moreRef}
        className="relative"
        data-testid="replay-row-more-actions"
        onKeyDown={(e) => {
          if (e.key === "Escape") closeMore();
        }}
      >
        <summary className="inline-flex h-7 cursor-pointer list-none items-center rounded-md px-2 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground">
          <MoreHorizontal className="h-3 w-3 mr-1" /> その他の操作
        </summary>
        <div className="absolute right-0 z-20 mt-1 flex w-56 flex-col gap-0.5 rounded-md border bg-background p-1 shadow-md">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 justify-start px-2 text-xs"
            title="このTraceをReplay Setに入れ、Simulation Workbenchを開く"
            onClick={() => {
              closeMore();
              setDialogMode("replay");
            }}
          >
            <Play className="h-3 w-3 mr-1" /> Replay
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 justify-start px-2 text-xs"
            title="このTraceで新しいReplay Setを作る、または既存のSetにこのTraceを加えた新しい版を作る"
            onClick={() => {
              closeMore();
              setDialogMode("add");
            }}
          >
            <ListPlus className="h-3 w-3 mr-1" /> Replay Setに追加
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 justify-start px-2 text-xs"
            title="Experiments の作成画面を開く（比較する patch はそこで入力します）"
            onClick={() => {
              closeMore();
              navigate(
                `/experiments?from_trace=${encodeURIComponent(traceId)}&from_component=${encodeURIComponent(componentId)}`,
              );
            }}
          >
            <FlaskConical className="h-3 w-3 mr-1" /> Experimentを作成
          </Button>
        </div>
      </details>
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
      toast.success(
        mode === "replay"
          ? "Replay Setを準備しました"
          : pickMode === "existing"
            ? `このTraceを加えた新しい版 (Set #${created.id}) を作成しました`
            : "Replay Setに追加しました",
      );
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
        <DialogTitle>{mode === "replay" ? "このTraceをReplayする" : "Replay Setに追加"}</DialogTitle>
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
            新しいReplay Set
          </Button>
          <Button
            size="sm"
            variant={pickMode === "existing" ? "default" : "outline"}
            onClick={() => setPickMode("existing")}
            disabled={!sets?.length}
          >
            既存のSetを元にする
          </Button>
        </div>
        {pickMode === "existing" ? (
          <div className="space-y-2">
            <Label>元にするReplay Set</Label>
            <Select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
              <option value="">Replay Setを選択...</option>
              {sets?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || `Set #${s.id}`} #{s.id} · {s.trace_ids.length}件 · {formatTimestamp(s.created_at)}
                </option>
              ))}
            </Select>
            {/* UX audit 2026-11-17 F6: Replay Sets are immutable -- the server
                only INSERTs -- so this never edits the chosen Set. */}
            <p className="text-xs text-muted-foreground" data-testid="replay-set-new-version-note">
              選んだSetは変更されません。そのTrace群にこのTraceを加えた新しいReplay Setを同じ名前で作成します。
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <Label>名前</Label>
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
          {submitting
            ? "保存中..."
            : mode === "replay"
              ? "Replay"
              : pickMode === "existing"
                ? "このTraceを加えた新しい版を作成"
                : "追加"}
        </Button>
      </div>
    </Dialog>
  );
}
