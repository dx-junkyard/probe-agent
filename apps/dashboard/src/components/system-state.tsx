import { useNavigate } from "react-router-dom";
import { AlertTriangle, Ban, CircleAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { SystemStateItem } from "@/api/types";

export function systemStateTarget(item: SystemStateItem): string | null {
  if (!item.target_ui) return null;
  const params = new URLSearchParams();
  // Reuse the established diagnostic focus contract. A projected diagnostic
  // (or native item with one linked check) must preserve its specific check
  // across navigation: multiple checks can share an anchor such as "build".
  if (item.related_checks.length === 1) params.set("diagnostic", item.related_checks[0]);
  if (item.target_ui.anchor) params.set("fix", item.target_ui.anchor);
  const query = params.toString();
  return `${item.target_ui.route}${query ? `?${query}` : ""}`;
}

function StateIcon({ severity }: { severity: SystemStateItem["severity"] }) {
  const className = "mt-0.5 h-4 w-4 shrink-0";
  if (severity === "error") return <XCircle className={`${className} text-red-600`} />;
  if (severity === "blocked") return <Ban className={`${className} text-orange-500`} />;
  if (severity === "warning") return <AlertTriangle className={`${className} text-yellow-600`} />;
  return <CircleAlert className={`${className} text-blue-600`} />;
}

export function SystemStateAction({
  item, onAction, disabled = false, className,
}: {
  item: SystemStateItem;
  onAction?: () => void;
  disabled?: boolean;
  className?: string;
}) {
  const navigate = useNavigate();
  const target = systemStateTarget(item);
  if (!target) return null;
  return (
    <Button
      size="sm"
      className={className}
      disabled={disabled}
      onClick={() => onAction ? onAction() : navigate(target)}
      data-testid={`system-state-action-${item.state_id}`}
    >
      {item.target_ui?.action_label || "対応する"}
    </Button>
  );
}

/** A canonical page-level projection. Its copy and target come only from StateItem. */
export function SystemStateBanner({
  item, onAction, disabled = false, testId = "system-state-banner",
}: {
  item: SystemStateItem | null | undefined;
  onAction?: () => void;
  disabled?: boolean;
  testId?: string;
}) {
  if (!item) return null;
  return (
    <Card data-testid={testId} className="border-amber-300 dark:border-amber-800">
      <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
        <div className="flex min-w-0 gap-2">
          <StateIcon severity={item.severity} />
          <div>
            <p className="text-sm font-medium">{item.summary}</p>
            {item.remediation && <p className="mt-1 text-sm text-muted-foreground">{item.remediation}</p>}
          </div>
        </div>
        <SystemStateAction item={item} onAction={onAction} disabled={disabled} />
      </CardContent>
    </Card>
  );
}
