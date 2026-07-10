import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useSystemDiagnostics } from "@/api/hooks";
import { DiagnosticSeverityIcon } from "@/components/diagnostics-badge";
import { cn } from "@/lib/utils";
import type { SystemDiagnosticCheck } from "@/api/types";

/**
 * Issue #115: reads the `?fix=<anchor>&diagnostic=<check_id>` query params set
 * when a user clicks a diagnostic in the System Settings Diagnostics dialog and
 * is routed to the screen where the problem is fixed.
 */
export function useDiagnosticFocus(): { anchor: string | null; checkId: string | null } {
  const [params] = useSearchParams();
  return { anchor: params.get("fix"), checkId: params.get("diagnostic") };
}

/**
 * Returns the diagnostic check the current focus points at, matched by check id
 * first and falling back to the anchor. Only returns a check while the focus
 * anchor matches, so a screen highlights at most one location at a time.
 */
export function useFocusedCheck(anchor: string): SystemDiagnosticCheck | null {
  const focus = useDiagnosticFocus();
  const { data } = useSystemDiagnostics();
  if (focus.anchor !== anchor) return null;
  const checks = data?.checks ?? [];
  return (
    checks.find((c) => c.check_id === focus.checkId && c.fix_anchor === anchor) ??
    checks.find((c) => c.fix_anchor === anchor) ??
    null
  );
}

/**
 * Applies a temporary highlight ring to the element for the matching anchor and
 * scrolls it into view. The ring fades after a few seconds so the page returns
 * to its normal appearance.
 */
export function useDiagnosticHighlight<T extends HTMLElement>(anchor: string) {
  const focus = useDiagnosticFocus();
  const ref = useRef<T>(null);
  // Key the highlight on the specific focus (anchor + check), not just the
  // anchor, so re-navigating to the same anchor for a different check re-fires
  // the scroll and ring. Records the focus token whose highlight has already
  // faded, so state is only updated from the timeout callback.
  const focusToken = focus.anchor === anchor ? `${anchor}:${focus.checkId ?? ""}` : null;
  const [fadedToken, setFadedToken] = useState<string | null>(null);

  useEffect(() => {
    if (!focusToken) return;
    ref.current?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    const timer = setTimeout(() => setFadedToken(focusToken), 4000);
    return () => clearTimeout(timer);
  }, [focusToken]);

  const active = focusToken !== null && fadedToken !== focusToken;
  return {
    ref,
    "data-diag-anchor": anchor,
    className: active
      ? "rounded-lg ring-2 ring-primary ring-offset-2 ring-offset-background transition-shadow"
      : "transition-shadow",
  };
}

/**
 * Inline callout rendered next to the fix location. Shows the problem ("原因")
 * and the next action ("次の操作") in Japanese, verbatim from the deterministic
 * diagnostics output — no client-side interpretation (Issue #101 rule).
 */
export function DiagnosticFixCallout({ anchor, className }: { anchor: string; className?: string }) {
  const check = useFocusedCheck(anchor);
  if (!check) return null;

  const tone =
    check.severity === "error"
      ? "border-destructive/50 bg-destructive/5"
      : check.severity === "blocked"
        ? "border-orange-500/50 bg-orange-500/5"
        : "border-yellow-500/50 bg-yellow-500/5";

  return (
    <div
      className={cn("rounded-lg border p-3 space-y-2 text-sm", tone, className)}
      data-testid={`diagnostic-callout-${anchor}`}
      role="status"
    >
      <div className="flex items-start gap-2">
        <DiagnosticSeverityIcon severity={check.severity} />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">{check.title}</p>
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">原因: </span>
            {check.detail}
          </p>
          {check.remediation && (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">次の操作: </span>
              {check.remediation}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Shared activation for a clicked diagnostic. `navigate` checks route to the
 * fix page with `?diagnostic=<id>&fix=<anchor>`; `dialog` checks (env var /
 * restart, no in-app control) are surfaced via `envCheck` so the caller can
 * render an EnvFixDialog. Used by both the badge dialog and the in-page
 * pipeline diagnostics so the two entry points behave identically.
 */
export function useDiagnosticActivate(options?: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const onNavigate = options?.onNavigate;
  const [envCheck, setEnvCheck] = useState<SystemDiagnosticCheck | null>(null);

  const activate = useCallback(
    (check: SystemDiagnosticCheck) => {
      if (check.fix_kind === "navigate" && check.fix_page) {
        const params = new URLSearchParams();
        params.set("diagnostic", check.check_id);
        if (check.fix_anchor) params.set("fix", check.fix_anchor);
        onNavigate?.();
        navigate(`${check.fix_page}?${params.toString()}`);
      } else {
        setEnvCheck(check);
      }
    },
    [navigate, onNavigate],
  );

  return { activate, envCheck, closeEnv: () => setEnvCheck(null) };
}
