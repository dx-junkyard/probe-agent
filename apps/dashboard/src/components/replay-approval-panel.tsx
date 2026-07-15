import { useState } from "react";
import { toast } from "sonner";
import { useReplayApproval, useApproveReplay, useRevokeReplayApproval } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";

// Shared human replay-approval gate (state guidance + approve/revoke),
// extracted from the Simulation Workbench (Issue #242 Phase D / #246) so
// AI Candidate Studio (Issue #252) can reuse the exact same gate and risk
// context instead of re-implementing it. Judgement stays server-side --
// this only displays the deterministic risk context and posts the human's
// approve/revoke decision (decision_method: manual, Principle 7).

export function ApprovalPanel({ componentId }: { componentId: string }) {
  const { data: approvalState } = useReplayApproval(componentId);
  const approve = useApproveReplay();
  const revoke = useRevokeReplayApproval();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (!approvalState) return <Skeleton className="h-14 w-full" />;

  if (!approvalState.active) {
    return (
      <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-xs space-y-2">
        <p className="font-medium text-amber-900 dark:text-amber-100">
          Replay is not approved for "{componentId}" -- runs are blocked until a human approves it.
        </p>
        <p className="text-muted-foreground">
          Next step: review the risk context and approve replay for this component.
        </p>
        <Button size="sm" onClick={() => setConfirmOpen(true)}>
          Review &amp; Approve
        </Button>
        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogHeader>
            <DialogTitle>Approve replay for "{componentId}"</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <p className="text-muted-foreground">{approvalState.risk_context.warning}</p>
            {approvalState.risk_context.probe_plan_points.length > 0 && (
              <ul className="text-xs space-y-1">
                {approvalState.risk_context.probe_plan_points.map((p) => (
                  <li key={p.point_id}>
                    plan #{p.plan_id}: side_effect_risk={p.side_effect_risk ?? "unknown"}, replayability=
                    {p.replayability ?? "unknown"}
                  </li>
                ))}
              </ul>
            )}
            <div className="space-y-1">
              <Label>Reason</Label>
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why is replay safe to approve for this component?"
              />
            </div>
            <Button
              className="w-full"
              disabled={!reason.trim() || approve.isPending}
              onClick={async () => {
                try {
                  await approve.mutateAsync({ componentId, reason: reason.trim() });
                  toast.success("Replay approved");
                  setConfirmOpen(false);
                  setReason("");
                } catch (e) {
                  toast.error(String(e));
                }
              }}
            >
              {approve.isPending ? "Approving..." : "Approve"}
            </Button>
          </div>
        </Dialog>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between rounded-md border p-2 text-xs">
      <span className="text-muted-foreground">Replay approved for "{componentId}".</span>
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          revoke
            .mutateAsync(componentId)
            .then(() => toast.success("Approval revoked"))
            .catch((e) => toast.error(String(e)))
        }
        disabled={revoke.isPending}
      >
        Revoke
      </Button>
    </div>
  );
}
