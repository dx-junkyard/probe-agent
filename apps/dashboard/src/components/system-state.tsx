import { useNavigate } from "react-router-dom";
import { AlertTriangle, Ban, CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useSystemState } from "@/api/hooks";
import { cn } from "@/lib/utils";
import type { SystemStateItem, UserPhase } from "@/api/types";

export function systemStateTarget(item: SystemStateItem): string | null {
  if (!item.target_ui) return null;
  // Some target_ui.route values already carry a query string (e.g.
  // "/interview?session=7" for the capability-hierarchy-empty item routed at
  // a specific Interview session) -- split it off first so the
  // diagnostic/fix params below are merged into the existing query instead
  // of appending a second "?" (Issue #239: this target is now consumed by
  // four surfaces -- banner, badge, notice, and the Pipeline Checklist CTA
  // -- so a malformed URL here would break navigation everywhere at once).
  const [path, existingQuery] = item.target_ui.route.split("?");
  const params = new URLSearchParams(existingQuery);
  // Reuse the established diagnostic focus contract. A projected diagnostic
  // (or native item with one linked check) must preserve its specific check
  // across navigation: multiple checks can share an anchor such as "build".
  if (item.related_checks.length === 1) params.set("diagnostic", item.related_checks[0]);
  if (item.target_ui.anchor) params.set("fix", item.target_ui.anchor);
  const query = params.toString();
  return `${path}${query ? `?${query}` : ""}`;
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

/**
 * Issue #239: fixed display labels for the three server-derived user phases
 * (`GET /system-state`'s `user_phase` / `phases`, Issue #237). The phase
 * *value* is always the server's; only this tiny value->label map lives on
 * the client (kept in one place so Issue #240's copy pass can consolidate
 * it). Falls back to the raw token for an unknown value rather than
 * guessing.
 */
export const USER_PHASE_LABELS: Record<UserPhase, string> = {
  setup: "必要最低限の設定",
  preparation: "診断準備",
  diagnosis: "診断",
};

/** Mirrors the server's fixed PHASE_ORDER (Issue #237) — a finite enum
 * ordering, not client-side state derivation. */
const PHASE_ORDER: UserPhase[] = ["setup", "preparation", "diagnosis"];

/**
 * Phase suppression for surfaces that project the raw `items` audit list
 * (Issue #239: the header badge). The server already phase-scopes
 * `primary_item` / `notification_items` / `page_items`, but keeps every
 * item in `items` for auditability — so a projection built from `items`
 * must apply the same withdrawal rule: hide items belonging to a phase
 * *after* the current one. Both compared values come verbatim from the
 * server; when either is missing (older Control Server), the item stays
 * visible rather than being guessed away.
 */
export function isItemInCurrentPhaseScope(
  item: SystemStateItem,
  userPhase: UserPhase | undefined,
): boolean {
  if (!userPhase || !item.phase) return true;
  const current = PHASE_ORDER.indexOf(userPhase);
  const own = PHASE_ORDER.indexOf(item.phase);
  if (current === -1 || own === -1) return true;
  return own <= current;
}

/**
 * Header strip showing the current user phase and each phase's completion
 * (Issue #239 Scope item 2). Renders nothing when the server has not
 * provided `user_phase`/`phases` (older Control Server or state not loaded)
 * -- it never derives a phase client-side.
 */
export function UserPhaseIndicator() {
  const { data } = useSystemState();
  if (!data?.user_phase || !data.phases?.length) return null;
  return (
    <div
      className="hidden items-center gap-1 md:flex"
      data-testid="user-phase-indicator"
      data-current-phase={data.user_phase}
      title={`現在のフェーズ: ${USER_PHASE_LABELS[data.user_phase] ?? data.user_phase}`}
    >
      {data.phases.map((p, i) => {
        const isCurrent = p.phase === data.user_phase;
        return (
          <span key={p.phase} className="flex items-center gap-1">
            {i > 0 && <span className="text-muted-foreground/50 text-xs">›</span>}
            <span
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                isCurrent
                  ? "bg-primary/10 font-medium text-primary"
                  : p.complete
                    ? "text-muted-foreground"
                    : "text-muted-foreground/60",
              )}
              data-testid={`user-phase-${p.phase}`}
              data-complete={p.complete}
              data-current={isCurrent}
            >
              {p.complete && <CheckCircle2 className="h-3 w-3 text-green-600" />}
              {USER_PHASE_LABELS[p.phase] ?? p.phase}
            </span>
          </span>
        );
      })}
    </div>
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
